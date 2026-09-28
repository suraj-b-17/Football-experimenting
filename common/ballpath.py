"""Ball-proxy path from on-ball events. The ONE implementation used by training
(events simulated from real tracking) and production (StatsBomb events), so the
model sees the same kind of ball input in both.

A ball event is a dict in the common (home-team own-goal-at-0) frame:
    {"t0", "x0", "y0", "t1", "x1", "y1", "team"}
t1 > t0 for passes/carries/shots (the ball travels start -> end over the real
duration); t1 == t0 for single touches.

Path rules:
  * during [t0, t1] the ball moves linearly from start to end;
  * between one event's end and the next event's start it is at rest at the
    previous end, and only in the last `tau` seconds before the next event does
    it move to the next start, tau = clamp(dist / BALL_TRAVEL_MPS, 0.3 s, gap).
    Recorded locations disagree by a metre or two all the time; this avoids an
    instantaneous jump without inventing a trajectory.
"""
import numpy as np

PITCH_X_M, PITCH_Y_M = 105.0, 68.0
BALL_TRAVEL_MPS = 15.0


def clean_events(evs):
    """Sort and clip overlaps: an event may not end after the next one starts."""
    evs = sorted((dict(e) for e in evs), key=lambda e: e["t0"])
    for i in range(len(evs) - 1):
        a, b = evs[i], evs[i + 1]
        if a["t1"] > b["t0"]:
            span = a["t1"] - a["t0"]
            f = (b["t0"] - a["t0"]) / span if span > 1e-9 else 1.0
            a["x1"] = a["x0"] + (a["x1"] - a["x0"]) * f
            a["y1"] = a["y0"] + (a["y1"] - a["y0"]) * f
            a["t1"] = b["t0"]
    return evs


def build_path(evs):
    """Returns knot arrays (T, X, Y) of a piecewise-linear path, T strictly increasing."""
    evs = clean_events(evs)
    T, X, Y = [], [], []

    def add(t, x, y):
        if T and t <= T[-1] + 1e-6:
            T[-1], X[-1], Y[-1] = max(T[-1], t), x, y
            return
        T.append(t); X.append(x); Y.append(y)

    for i, e in enumerate(evs):
        if i > 0:
            p = evs[i - 1]
            gap = e["t0"] - p["t1"]
            dist = float(np.hypot(e["x0"] - p["x1"], e["y0"] - p["y1"]))
            if gap > 1e-6:
                tau = min(gap, max(0.3, dist / BALL_TRAVEL_MPS))
                add(e["t0"] - tau, p["x1"], p["y1"])
        add(e["t0"], e["x0"], e["y0"])
        if e["t1"] > e["t0"] + 1e-6:
            add(e["t1"], e["x1"], e["y1"])
    return np.array(T), np.array(X), np.array(Y)


def sample(path, t):
    T, X, Y = path
    return np.interp(t, T, X), np.interp(t, T, Y)


def velocity(path, t, h=0.5):
    x0, y0 = sample(path, np.asarray(t) - h)
    x1, y1 = sample(path, np.asarray(t) + h)
    return (x1 - x0) / (2 * h), (y1 - y0) / (2 * h)


def possession(evs, t, team_values):
    """Team of the most recent ball event at or before t (first event's team before it)."""
    evs = sorted(evs, key=lambda e: e["t0"])
    t0 = np.array([e["t0"] for e in evs])
    teams = np.array([team_values.index(e["team"]) for e in evs])
    idx = np.clip(np.searchsorted(t0, t, side="right") - 1, 0, len(evs) - 1)
    return teams[idx]


def to_own_frame(x, y, is_home):
    if is_home:
        return x, y
    return PITCH_X_M - x, PITCH_Y_M - y
