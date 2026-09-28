"""The ball-conditioned mean function's training rows:
    offset(anchor, ball, possession) = actual_position - anchor
built identically from every tracking source (Metrica, DFL/IDSSE, SkillCorner).

Sources with visibility gaps (SkillCorner broadcast tracking) mark each player
and ball sample with a `detected` flag. Only real observations become rows:
a player's target position must come from a frame where that player was
actually detected, the ball feature from a detected ball within BALL_MAX_GAP_S,
and the player's slowly-varying anchor is computed from detected samples
only — never from the provider's own extrapolation of off-screen players.
Fully tracked sources (Metrica, IDSSE) have no flag and use every sample.

Model fitting itself happens in pool_train.py (pooled across all sources).
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PITCH_X_M = 105.0
PITCH_Y_M = 68.0
FEATS = ["anchor_x", "anchor_y", "ball_rel_x", "ball_rel_y", "possession"]
TARGETS = ["offset_x", "offset_y"]
PLAYER_MAX_GAP_S = 0.15  # a grid sample must sit on (±1 frame of) a real detection
BALL_MAX_GAP_S = 0.5     # ... and the ball must have been seen within this long

# Hyperparameters used for EVERY model in this project (benchmark, LOMO folds,
# production). `max_samples` caps each tree's bootstrap at 200k rows so a
# 3M-row pool trains in minutes rather than hours; it is applied identically
# to every configuration compared, so comparisons stay apples-to-apples.
RF_PARAMS = dict(n_estimators=300, max_depth=14, min_samples_leaf=10,
                 max_samples=200_000, n_jobs=-1, random_state=0)

def build_possession_series(events):
    return events[["Start Time [s]", "Period", "Team"]].dropna().sort_values("Start Time [s]")

def possession_at(ev, period, t):
    sub = ev[ev["Period"] == period]
    idx = sub["Start Time [s]"].searchsorted(t, side="right") - 1
    if idx < 0 or idx >= len(sub):
        return None
    return sub.iloc[idx]["Team"]

def rolling_anchor(t, x, y, window_s=120.0):
    """Slowly-varying positional baseline: median position in a +/-window_s/2
    window around each sample. Deliberately NOT the whole-match median —
    players' tactical baseline drifts (subs, game state, half-to-half
    tactical tweaks), and we want the *offset* to capture ball-reactive
    movement, not that slow drift."""
    n = len(t)
    half = window_s / 2.0
    lo = np.searchsorted(t, t - half)
    hi = np.searchsorted(t, t + half)
    ax = np.empty(n)
    ay = np.empty(n)
    for i in range(n):
        ax[i] = np.median(x[lo[i]:hi[i]])
        ay[i] = np.median(y[lo[i]:hi[i]])
    return ax, ay

def _near(sample_t, grid, max_gap):
    """For each grid time, is there a sample within max_gap seconds?"""
    if len(sample_t) == 0:
        return np.zeros(len(grid), dtype=bool)
    idx = np.clip(np.searchsorted(sample_t, grid), 1, len(sample_t) - 1)
    gap = np.minimum(np.abs(sample_t[idx] - grid), np.abs(sample_t[idx - 1] - grid))
    return gap <= max_gap

def build_training_rows(game, sample_every_s=1.0, anchor_window_s=120.0):
    players, ball, events = game["players"], game["ball"], game["events"]
    flips = game.get("flips", {})
    ev = build_possession_series(events)
    has_det = "detected" in players.columns

    rows = []
    for period in sorted(players["period"].unique()):
        pplayers = players[players["period"] == period]
        pball = ball[ball["period"] == period].sort_values("t")
        if has_det:
            pball = pball[pball["detected"]]
        pball = pball.dropna(subset=["x_m", "y_m"])
        if len(pball) < 2:
            continue
        t_min, t_max = pplayers["t"].min(), pplayers["t"].max()
        grid = np.arange(t_min, t_max, sample_every_s)
        ball_ok = _near(pball["t"].values, grid, BALL_MAX_GAP_S) if has_det else np.ones(len(grid), bool)

        # Ball tracking is absolute (never flipped); flip it into each team's
        # own frame separately, matching the already-flipped player positions.
        ball_x_raw = np.interp(grid, pball["t"], pball["x_m"])
        ball_y_raw = np.interp(grid, pball["t"], pball["y_m"])
        poss_team = np.array([possession_at(ev, period, t) for t in grid], dtype=object)

        for player_id, pgrp in pplayers.groupby("player_id"):
            pgrp = pgrp.sort_values("t")
            if has_det:
                pgrp = pgrp[pgrp["detected"]]
            pgrp = pgrp.dropna(subset=["x_m", "y_m"])
            if len(pgrp) < 2:
                continue
            team = pgrp["team"].iloc[0]
            if flips.get((team, period), False):
                ball_x, ball_y = PITCH_X_M - ball_x_raw, PITCH_Y_M - ball_y_raw
            else:
                ball_x, ball_y = ball_x_raw, ball_y_raw
            pt = pgrp["t"].values
            on_pitch = (grid >= pt[0]) & (grid <= pt[-1])
            keep = on_pitch & ball_ok & (_near(pt, grid, PLAYER_MAX_GAP_S) if has_det else True)
            if keep.sum() < 2:
                continue
            g = grid[keep]
            px = np.interp(g, pt, pgrp["x_m"].values)
            py = np.interp(g, pt, pgrp["y_m"].values)
            ax, ay = rolling_anchor(g, px, py, anchor_window_s)
            poss_flag = (poss_team[keep] == team).astype(float)
            rows.append(pd.DataFrame({
                "period": period, "t": g,
                "anchor_x": ax, "anchor_y": ay,
                "ball_rel_x": ball_x[keep] - ax, "ball_rel_y": ball_y[keep] - ay,
                "possession": poss_flag,
                "offset_x": px - ax, "offset_y": py - ay,
            }))
    return pd.concat(rows, ignore_index=True)
