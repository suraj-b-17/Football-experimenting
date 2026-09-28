"""Applies the trained ball-conditioned mean model + OU/Kalman smoother
(models/mean_model.joblib, fit on real tracking data — see training/) to a
single StatsBomb match, producing a grid of (t, x, y, sd) per player.

StatsBomb has no continuous ball tracking for this competition (confirmed:
no 360 data, see loader_statsbomb.py) — the ball's position is approximated
here the same way the model's OWN application always has been: wherever the
acting player's event happened is where the ball roughly was. StatsBomb
events are already in each team's own attacking-direction frame (unlike raw
camera coordinates), so building one shared ball trajectory requires
normalizing to a common frame first, then flipping back per-team at the
point of use — the same fix this project's previous version had to
discover the hard way (see the old README's "ball-frame bug").

`OU_PARAMS`/`CALIB` below come from training/fit_ou_params.py and
benchmark.py, run against models/mean_model_no_metrica_game2.joblib — see
BENCHMARKS.md for how they were produced and what they measure.
"""
import json
import os
import sys

import joblib
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "training"))
from paths import MODEL_DIR
from build_match import build_tracks, detect_stoppages, PITCH_X_M, PITCH_Y_M
from kalman_ou import smooth_track

GRID_STEP_S = 1.0
FEATS = ["anchor_x", "anchor_y", "ball_rel_x", "ball_rel_y", "possession"]

# From training/fit_ou_params.py against mean_model_no_metrica_game2.joblib —
# see BENCHMARKS.md.
OU_THETA_X, OU_SIGMA_X = 0.0247, 2.058
OU_THETA_Y, OU_SIGMA_Y = 0.0311, 1.658
OBS_VAR = 2.25  # ~1.5m tagging noise, per axis (same assumption as before, unchanged)

# From training/benchmark.py's calibration pass — see BENCHMARKS.md.
CALIB_K50, CALIB_K68, CALIB_K90 = 1.224, 1.681, 2.773

def gap_distance_meters(dx, dy):
    return float(np.hypot(dx, dy))  # already in meters, unlike the old 0-100-unit pipeline

def build_ball_and_possession(match, home_id):
    """A single ball-position proxy trajectory in a COMMON (home-perspective)
    frame, built from every event's own actor position, plus a possession
    series read directly from StatsBomb's own `possession_team` field
    (much more reliable than the old project's "last team to touch it"
    inference, since StatsBomb tags this explicitly per possession phase)."""
    pts = []
    for e in match["events"]:
        if e["x"] is None or e["teamId"] is None:
            continue
        is_home = e["teamId"] == home_id
        cx = e["x"] if is_home else PITCH_X_M - e["x"]
        cy = e["y"] if is_home else PITCH_Y_M - e["y"]
        pts.append((e["t"], cx, cy, e.get("possessionTeamId")))
    pts.sort(key=lambda p: p[0])
    bt = np.array([p[0] for p in pts])
    bx = np.array([p[1] for p in pts])
    by = np.array([p[2] for p in pts])
    poss_team = np.array([p[3] for p in pts])
    return bt, bx, by, bt, poss_team

def smooth_all(tracks, team_id, is_home, bt, bx, by, poss_t, poss_team, model, max_t):
    out = {}
    for track in tracks:
        if track["teamId"] != team_id:
            continue
        wp = track["waypoints"]
        if len(wp) < 2:
            continue
        wt = np.array([w[0] for w in wp])
        wx = np.array([w[1] for w in wp])
        wy = np.array([w[2] for w in wp])
        anchor_x, anchor_y = np.median(wx), np.median(wy)

        enter = track["enter"] if track["enter"] is not None else wt[0]
        exit_ = track["exit"] if track["exit"] is not None else max_t
        grid = np.arange(enter, max(exit_, enter + GRID_STEP_S), GRID_STEP_S)
        if len(grid) < 2:
            grid = np.array([enter, max(exit_, enter + 1.0)])

        gbx = np.interp(grid, bt, bx)
        gby = np.interp(grid, bt, by)
        if not is_home:
            gbx = PITCH_X_M - gbx
            gby = PITCH_Y_M - gby
        idx = np.searchsorted(poss_t, grid, side="right") - 1
        idx = np.clip(idx, 0, len(poss_t) - 1)
        poss_flag = (poss_team[idx] == team_id).astype(float)

        X = np.column_stack([np.full(len(grid), anchor_x), np.full(len(grid), anchor_y),
                             gbx - anchor_x, gby - anchor_y, poss_flag])
        pred = model.predict(X)
        mu_x = np.clip(anchor_x + pred[:, 0], -3, PITCH_X_M + 3)
        mu_y = np.clip(anchor_y + pred[:, 1], -3, PITCH_Y_M + 3)

        # mu(t) is "expected position given the CURRENT ball state" — rate-
        # limit it so a sudden possession flip can't teleport a team's
        # expected shape faster than they could actually reposition.
        MU_MAX_MPS = 6.5
        for i in range(1, len(grid)):
            dt = grid[i] - grid[i - 1]
            dx, dy = mu_x[i] - mu_x[i - 1], mu_y[i] - mu_y[i - 1]
            dist = gap_distance_meters(dx, dy)
            max_step = MU_MAX_MPS * dt
            if dist > max_step and dist > 0:
                sc = max_step / dist
                mu_x[i] = mu_x[i - 1] + dx * sc
                mu_y[i] = mu_y[i - 1] + dy * sc

        kx, ky, px, py = smooth_track(grid, mu_x, mu_y, wt, wx, wy,
                                      OU_THETA_X, OU_SIGMA_X, OU_THETA_Y, OU_SIGMA_Y, OBS_VAR)
        kx = np.clip(kx, -5, PITCH_X_M + 5)
        ky = np.clip(ky, -5, PITCH_Y_M + 5)
        sd = np.sqrt(np.maximum(px, 0) + np.maximum(py, 0))

        out[str(track["id"])] = {
            "t": [round(v, 1) for v in grid.tolist()],
            "x": [round(v, 2) for v in kx.tolist()],
            "y": [round(v, 2) for v in ky.tolist()],
            "sd": [round(v, 2) for v in sd.tolist()],
        }
    return out

def apply(match):
    tracks = build_tracks(match)
    model_path = os.path.join(MODEL_DIR, "mean_model.joblib")
    model = joblib.load(model_path)
    home_id = match["home"]["id"]
    bt, bx, by, poss_t, poss_team = build_ball_and_possession(match, home_id)
    home_smoothed = smooth_all(tracks, home_id, True, bt, bx, by, poss_t, poss_team, model, match["maxT"])
    away_smoothed = smooth_all(tracks, match["away"]["id"], False, bt, bx, by, poss_t, poss_team, model, match["maxT"])
    return tracks, home_smoothed, away_smoothed

if __name__ == "__main__":
    from loader_statsbomb import load_match
    m = load_match(sys.argv[1] if len(sys.argv) > 1 else "3754348")
    tracks, home_s, away_s = apply(m)
    print(f"{len(home_s)} home + {len(away_s)} away players smoothed")
    any_track = next(iter(home_s.values()))
    print("sample grid len:", len(any_track["t"]), "sd range:",
          min(min(v["sd"]) for v in {**home_s, **away_s}.values()),
          max(max(v["sd"]) for v in {**home_s, **away_s}.values()))
