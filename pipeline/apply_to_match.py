"""Applies the sparsity-matched model to one StatsBomb match.

Same feature function (common.features), same ball proxy (common.ballpath) and
same smoother (common.smoother) as training/train_v2.py. Per player and per
on-pitch segment (split at period boundaries and at Player Off / Player On), it
produces the smoothed track on the union of a 1 s grid and the exact anchor
times, so every real anchor is on the displayed path at its own timestamp.

Kickoffs (period starts and restarts after goals) add SOFT anchors from the
kickoff template learned from real tracking (models/kickoff_template.json),
clamped to the laws (own half; receiving team outside the centre circle), with
the template's residual SD as their uncertainty. A real event of that player at
the kickoff always takes precedence.
"""
import json
import os
import sys

import joblib
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from paths import MODEL_DIR
from build_match import build_tracks, ball_events, kickoffs, role_at
from common import ballpath, smoother
from common.features import (side_features, stage2_features, role_from_position_name, ROLE_CODE,
                             FEATS_BASE, FEATS_COUPLED, FEATS_STAGE2)

PITCH_X_M, PITCH_Y_M = 105.0, 68.0
GRID_STEP_S = 1.0
HARD_KINDS = {"event", "carry_end", "reception", "freeze"}
OFFCAM_VAR = 1.5 ** 2
CENTRE = (52.5, 34.0)
_BUNDLE = None


def bundle():
    global _BUNDLE
    if _BUNDLE is None:
        _BUNDLE = joblib.load(os.path.join(MODEL_DIR, "v2_bundle.joblib"))
        _BUNDLE["kickoff"] = json.load(open(os.path.join(MODEL_DIR, "kickoff_template.json")))
    return _BUNDLE


def feats_for(name):
    return {"base": FEATS_BASE, "coupled": FEATS_COUPLED, "stage2": FEATS_STAGE2}[name]


def kickoff_position(tmpl, role, kicking, anchors_after):
    g = tmpl["groups"].get(f"{role}|{int(kicking)}")
    if g is None:
        return None
    if len(anchors_after) and "coef_x" in g:
        mx, my = np.median(anchors_after[:, 1]), np.median(anchors_after[:, 2])
        x, y = np.polyval(g["coef_x"], mx), np.polyval(g["coef_y"], my)
        sx, sy = g["sd_x"], g["sd_y"]
    else:
        x, y, sx, sy = g["fallback_x"], g["fallback_y"], g["fallback_sd_x"], g["fallback_sd_y"]
    x = float(np.clip(x, 0.5, CENTRE[0] - 0.3))
    y = float(np.clip(y, 0.5, PITCH_Y_M - 0.5))
    if not kicking:
        d = np.hypot(x - CENTRE[0], y - CENTRE[1])
        if d < 9.15:
            ang = np.arctan2(y - CENTRE[1], x - CENTRE[0]) if d > 1e-6 else np.pi
            x, y = CENTRE[0] + 9.15 * np.cos(ang), CENTRE[1] + 9.15 * np.sin(ang)
            x = min(x, CENTRE[0] - 0.3)
    return x, y, sx ** 2, sy ** 2


def _segments(intervals, lo, hi):
    out = []
    for a, b in intervals:
        s, e = max(a, lo), min(b, hi)
        if e - s >= 1.0:
            out.append((s, e))
    return out


def apply(match, use_freeze=True, freeze_exclude=frozenset(), mode=None):
    B = bundle()
    featset = feats_for(mode or B["featset"])
    tracks = build_tracks(match, use_freeze=use_freeze, freeze_exclude=freeze_exclude)
    bev = ball_events(match)
    path = ballpath.build_path(bev)
    home_id, away_id = match["home"]["id"], match["away"]["id"]
    kicks = kickoffs(match)
    tmpl = B["kickoff"]

    hard = {}
    for tr in tracks:
        A = np.array([a[:3] for a in tr["anchors"] if a[3] in HARD_KINDS or a[3] == "event_offcam"]).reshape(-1, 3)
        hard[tr["id"]] = A[np.argsort(A[:, 0], kind="stable")] if len(A) else A

    out = {tr["id"]: {"segments": [], "kickoff_anchors": []} for tr in tracks}
    for period, (p0, p1) in match["periodBounds"].items():
        if p1 - p0 < 5:
            continue
        G = np.arange(np.ceil(p0), p1, GRID_STEP_S)
        if len(G) < 2:
            continue
        poss_idx = ballpath.possession(bev, G, [home_id, away_id])

        def side(team_id):
            d = {}
            for tr in tracks:
                if tr["teamId"] != team_id:
                    continue
                act = np.zeros(len(G), bool)
                for s, e in _segments(tr["intervals"], p0, p1):
                    act |= (G >= s) & (G <= e)
                if not act.any():
                    continue
                roles = np.array([ROLE_CODE[role_from_position_name(role_at(tr["roles"], t))] if tr["roles"] else 2
                                  for t in G], dtype=float)
                d[tr["id"]] = {"anchors": hard[tr["id"]], "role": roles, "active": act}
            return d

        sides = {home_id: side(home_id), away_id: side(away_id)}
        dfs = {}
        for team_id, is_home in ((home_id, True), (away_id, False)):
            opp = away_id if is_home else home_id
            dfs[team_id] = side_features(G, sides[team_id], sides[opp], path, poss_idx == (0 if is_home else 1), is_home)
        preds = {}
        for team_id, df in dfs.items():
            if df.empty:
                continue
            m1 = B["models"]["stage1" if B["featset"] == "stage2" else "main"]
            f1 = FEATS_COUPLED if B["featset"] == "stage2" else featset
            X = df[f1].values
            preds[team_id] = (df["base_x"].values + m1[0].predict(X), df["base_y"].values + m1[1].predict(X))
        if B["featset"] == "stage2":
            for team_id, df in list(dfs.items()):
                opp = away_id if team_id == home_id else home_id
                if df.empty or dfs[opp].empty:
                    continue
                d2 = stage2_features(df, dfs[opp], preds[team_id][0], preds[opp][0])
                X = d2[FEATS_STAGE2].values
                m2 = B["models"]["main"]
                preds[team_id] = (df["base_x"].values + m2[0].predict(X), df["base_y"].values + m2[1].predict(X))

        for team_id, df in dfs.items():
            if df.empty:
                continue
            is_home = team_id == home_id
            mx_all = np.clip(preds[team_id][0], -2, PITCH_X_M + 2)
            my_all = np.clip(preds[team_id][1], -2, PITCH_Y_M + 2)
            df = df.assign(_mx=mx_all, _my=my_all)
            for pid, sub in df.groupby("pid"):
                tr = next(t for t in tracks if t["id"] == pid)
                A = hard[pid]
                kinds = {round(a[0], 3): a[3] for a in tr["anchors"]}
                for s, e in _segments(tr["intervals"], p0, p1):
                    m = (sub["t"].values >= s - 1e-6) & (sub["t"].values <= e + 1e-6)
                    if m.sum() < 2:
                        continue
                    # Smooth on the genuinely uniform 1 Hz samples ONLY, then linearly
                    # interpolate onto the segment's start/end (which land a fraction of
                    # a second after the last grid tick at every boundary: substitutions,
                    # cards, kickoffs, match/period end). smooth_mean's Gaussian filter is
                    # index-based, not time-based; running it after inserting a near-
                    # duplicate endpoint made it treat that endpoint as "one more equally
                    # spaced sample" and continue the trend, producing a multi-metre jump
                    # in a few milliseconds at every boundary (found via QA, see
                    # CHANGELOG_fix.md D10).
                    Gs_reg = sub["t"].values[m]
                    mx_reg = smoother.smooth_mean(Gs_reg, sub["_mx"].values[m], smoother.MU_SMOOTH_S)
                    my_reg = smoother.smooth_mean(Gs_reg, sub["_my"].values[m], smoother.MU_SMOOTH_S)
                    Gs = np.unique(np.concatenate([[s], Gs_reg, [e]]))
                    mx = np.interp(Gs, Gs_reg, mx_reg)
                    my = np.interp(Gs, Gs_reg, my_reg)
                    inseg = A[(A[:, 0] >= s - 1e-6) & (A[:, 0] <= e + 1e-6)] if len(A) else A
                    var = np.array([OFFCAM_VAR if kinds.get(round(t, 3)) == "event_offcam" else smoother.HARD_VAR
                                    for t in inseg[:, 0]]) if len(inseg) else np.zeros(0)
                    obs = [np.column_stack([inseg, var, var])] if len(inseg) else []
                    for (tk, pk, kteam) in kicks:
                        if pk != period or not (s - 1e-6 <= tk <= e + 1e-6):
                            continue
                        if len(A) and np.min(np.abs(A[:, 0] - tk)) < 1.0:
                            continue
                        role = role_from_position_name(role_at(tr["roles"], tk)) if tr["roles"] else "MID"
                        after = A[(A[:, 0] >= tk) & (A[:, 0] <= tk + tmpl["window_s"])] if len(A) else A
                        kp = kickoff_position(tmpl, role, kteam == team_id, after)
                        if kp:
                            obs.append(np.array([[tk, kp[0], kp[1], kp[2], kp[3]]]))
                            out[pid]["kickoff_anchors"].append([round(tk, 3), round(kp[0], 2), round(kp[1], 2)])
                    obs = np.vstack(obs) if obs else np.zeros((0, 5))
                    U, x, y, sd = smoother.smooth(Gs, mx, my, obs, B["ou"])
                    x = np.clip(x, -2, PITCH_X_M + 2); y = np.clip(y, -2, PITCH_Y_M + 2)
                    out[pid]["segments"].append({"t": U, "x": x, "y": y, "sd": sd, "period": period})
    return tracks, out, bev, kicks


if __name__ == "__main__":
    from loader_statsbomb import load_match
    m = load_match(sys.argv[1] if len(sys.argv) > 1 else "3754129")
    tracks, out, bev, kicks = apply(m)
    n = sum(len(s["t"]) for v in out.values() for s in v["segments"])
    print(len(tracks), "tracks,", n, "smoothed samples,", len(kicks), "kickoffs")
