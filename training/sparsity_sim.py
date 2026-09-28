"""Simulates StatsBomb-like sparse anchors and on-ball events from real tracking.

What StatsBomb records, and how it is mimicked on a tracking period (5 Hz):
  * Ball control: the player nearest the ball within R_CTRL controls it. Each
    control spell [a, b] yields a Ball Receipt at a and a Pass/Carry end at b,
    tagged at the BALL's location (as StatsBomb tags events), plus a carry
    a -> b if the ball moved. Between spells the ball travels as a "pass" from
    the end of one spell to the start of the next (flight capped at 6 s, after
    which it rests, like an out-of-play ball waiting for a restart).
  * Off-ball events (Pressure, Duel, Dribbled Past, ...): during each spell the
    nearest opponent within R_PRESS gets an anchor at his true position (plus
    tagging noise) with probability P_PRESS, a second-nearest with P_PRESS2.
Parameters are calibrated so per-role gap distributions and anchors/90 match
the real season (training/real_sparsity.json) — see calibrate_sparsity.py.
"""
import numpy as np

PITCH_X_M, PITCH_Y_M = 105.0, 68.0
DEFAULT_PARAMS = {"r_ctrl": 1.5, "min_spell_s": 0.2, "merge_gap_s": 0.4, "r_press": 5.0,
                  "p_press": 0.35, "p_press2": 0.1, "tag_noise_m": 0.5, "max_flight_s": 6.0}


def _fill(arr, known):
    """Nearest-known fill for ball positions used as event locations."""
    idx = np.where(known, np.arange(len(known)), 0)
    np.maximum.accumulate(idx, out=idx)
    return arr[idx]


def simulate(P, params=None, rng=None):
    """P: one prepared period (training.tracking_prep). Returns
    {"anchors": {pid: (n,3) own-frame}, "ball_events": [...home frame, team 'Home'/'Away'],
     "kind": {pid: list of 'touch'/'press'}}."""
    prm = dict(DEFAULT_PARAMS, **(params or {}))
    rng = rng or np.random.default_rng(0)
    T, home, known = P["T"], P["home"], P["known"]
    ball, bk = P["ball_home"], P["ball_known"]
    if not bk.any():
        return {"anchors": {}, "ball_events": [], "kind": {}}
    ballf = _fill(ball, bk)
    dt = T[1] - T[0]
    D = np.hypot(home[:, :, 0] - ball[None, :, 0], home[:, :, 1] - ball[None, :, 1])
    D[~known] = np.inf
    D[:, ~bk] = np.inf
    near = np.argmin(D, 0)
    ctrl = np.where(D[near, np.arange(len(T))] <= prm["r_ctrl"], near, -1)

    # spells: runs of one controller, bridging short gaps with the same controller
    spells = []
    i, n = 0, len(T)
    while i < n:
        if ctrl[i] < 0:
            i += 1; continue
        c, a, j = ctrl[i], i, i
        while j + 1 < n:
            if ctrl[j + 1] == c:
                j += 1; continue
            k = j + 1
            while k < n and ctrl[k] < 0 and (k - j) * dt <= prm["merge_gap_s"]:
                k += 1
            if k < n and ctrl[k] == c and (k - j - 1) * dt <= prm["merge_gap_s"]:
                j = k; continue
            break
        if (j - a + 1) * dt >= prm["min_spell_s"]:
            spells.append((c, a, j))
        i = j + 1

    teams = P["team"]
    is_home = np.array([t == "Home" for t in teams])
    anchors = {pid: [] for pid in P["pids"]}
    kind = {pid: [] for pid in P["pids"]}

    def own(pi, xy):
        return (xy[0], xy[1]) if is_home[pi] else (PITCH_X_M - xy[0], PITCH_Y_M - xy[1])

    def tag(xy):
        return xy + rng.normal(0, prm["tag_noise_m"], 2)

    evs = []
    for s, (c, a, b) in enumerate(spells):
        pid = P["pids"][c]
        ta, tb = T[a], T[b]
        ba, bb = ballf[a], ballf[b]
        team = teams[c]
        evs.append({"t0": ta, "x0": ba[0], "y0": ba[1], "t1": ta, "x1": ba[0], "y1": ba[1], "team": team})
        anchors[pid].append((ta, *own(c, tag(ba)))); kind[pid].append("touch")
        if tb - ta >= 0.4 and np.hypot(*(bb - ba)) >= 1.0:
            evs.append({"t0": ta, "x0": ba[0], "y0": ba[1], "t1": tb, "x1": bb[0], "y1": bb[1], "team": team})
        if tb - ta >= 0.2:
            anchors[pid].append((tb, *own(c, tag(bb)))); kind[pid].append("touch")
        if s + 1 < len(spells):
            tn = T[spells[s + 1][1]]
            flight = min(tn - tb, prm["max_flight_s"])
            if flight > 0:
                kf = min(int(round((tb + flight - T[0]) / dt)), n - 1)
                evs.append({"t0": tb, "x0": bb[0], "y0": bb[1], "t1": tb + flight,
                            "x1": ballf[kf][0], "y1": ballf[kf][1], "team": team})
        # off-ball: nearest opponents to the controller during the spell
        opp = np.nonzero(is_home != is_home[c])[0]
        seg = slice(a, b + 1)
        cx, cy = home[c, seg, 0], home[c, seg, 1]
        dd = np.hypot(home[opp, seg, 0] - cx[None], home[opp, seg, 1] - cy[None])
        dd[~known[opp, seg]] = np.inf
        if dd.size == 0:
            continue
        mins = dd.min(1)
        order = np.argsort(mins)
        for rank, prob in ((0, prm["p_press"]), (1, prm["p_press2"])):
            if rank >= len(order) or mins[order[rank]] > prm["r_press"] or rng.random() >= prob:
                continue
            oi = opp[order[rank]]
            fk = a + int(np.argmin(dd[order[rank]]))
            anchors[P["pids"][oi]].append((T[fk], *own(oi, tag(home[oi, fk].copy()))))
            kind[P["pids"][oi]].append("press")

    out = {}
    for pid, lst in anchors.items():
        if lst:
            arr = np.array(sorted(lst, key=lambda r: r[0]))
            keep = np.concatenate([[True], np.diff(arr[:, 0]) > 0.05])
            out[pid] = arr[keep]
        else:
            out[pid] = np.zeros((0, 3))
    return {"anchors": out, "ball_events": evs, "kind": kind}


def gap_stats(sims_and_preps):
    """Per-role gaps (s) and anchors/90 from [(sim, prep_period), ...]."""
    gaps, per90 = {}, {}
    for sim, P in sims_and_preps:
        for i, pid in enumerate(P["pids"]):
            role = P["role"].get(pid)
            if role is None:
                continue
            mins = P["on_pitch"][i].sum() * (P["T"][1] - P["T"][0]) / 60
            if mins < 10:
                continue
            A = sim["anchors"].get(pid, np.zeros((0, 3)))
            per90.setdefault(role, []).append(len(A) / mins * 90)
            if len(A) > 1:
                g = np.diff(A[:, 0]); gaps.setdefault(role, []).extend(g[g > 0.05].tolist())
    return gaps, per90
