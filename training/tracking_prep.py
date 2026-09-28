"""Resamples one real tracking match to 5 Hz arrays, per period, cached.

Per period the prepared dict holds:
  T            (n,) seconds
  pids         list of player ids, team[i] in {"Home","Away"}
  own          (p, n, 2) own-frame positions (NaN where not known)
  home         (p, n, 2) the same in the Home team's own frame
  known        (p, n) bool: a real observation within 0.15 s (detected-only for SkillCorner)
  on_pitch     (p, n) bool: between the player's first and last sample in the period
  ball_home    (n, 2) ball in the Home frame, ball_known (n,) bool
  role         {pid: "GK"/"DEF"/"MID"/"FWD"} from TRUE positions in that period
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from paths import cache

PITCH_X_M, PITCH_Y_M = 105.0, 68.0
HZ = 5.0
PREP_VERSION = "p1"
KNOWN_GAP_S = 0.15


def load(source, mid):
    import loader_metrica, loader_idsse, loader_skillcorner
    return {"metrica": loader_metrica.load_game, "idsse": loader_idsse.load_match,
            "skillcorner": loader_skillcorner.load_match}[source](mid)


def _interp_known(ts, xs, ys, T, max_gap):
    x = np.interp(T, ts, xs, left=np.nan, right=np.nan)
    y = np.interp(T, ts, ys, left=np.nan, right=np.nan)
    idx = np.clip(np.searchsorted(ts, T), 1, len(ts) - 1)
    gap = np.minimum(np.abs(ts[idx] - T), np.abs(ts[idx - 1] - T))
    known = (gap <= max_gap) & ~np.isnan(x)
    return x, y, known


def assign_roles(own, on_pitch, pids):
    """GK = player furthest from halfway on average; outfield by median x rank:
    deepest 40 % DEF, next 40 % MID, rest FWD (4-4-2-sized split of 10)."""
    med = {}
    for i, pid in enumerate(pids):
        v = own[i, :, 0][on_pitch[i] & ~np.isnan(own[i, :, 0])]
        if len(v) >= 50:
            med[pid] = float(np.median(v))
    roles = {}
    if not med:
        return roles
    gk = min(med, key=lambda p: med[p])
    roles[gk] = "GK"
    rest = sorted((p for p in med if p != gk), key=lambda p: med[p])
    n = len(rest)
    for k, p in enumerate(rest):
        q = k / max(n, 1)
        roles[p] = "DEF" if q < 0.4 else "MID" if q < 0.8 else "FWD"
    return roles


def prepare(game):
    players, ball, flips = game["players"], game["ball"], game.get("flips", {})
    has_det = "detected" in players.columns
    out = {}
    for period in sorted(players["period"].unique()):
        pp = players[players["period"] == period]
        bb = ball[ball["period"] == period].sort_values("t")
        if has_det:
            bb = bb[bb["detected"]]
        bb = bb.dropna(subset=["x_m", "y_m"])
        if len(bb) < 10:
            continue
        t0, t1 = max(pp["t"].min(), bb["t"].min()), min(pp["t"].max(), bb["t"].max())
        T = np.arange(np.ceil(t0 * HZ) / HZ, t1, 1.0 / HZ)
        bx, by, bk = _interp_known(bb["t"].values, bb["x_m"].values, bb["y_m"].values, T, 0.5)
        if flips.get(("Home", period), False):
            bx, by = PITCH_X_M - bx, PITCH_Y_M - by
        pids, teams, own, known, onp = [], [], [], [], []
        for pid, g in pp.groupby("player_id"):
            g = g.sort_values("t")
            span = (T >= g["t"].iloc[0]) & (T <= g["t"].iloc[-1])
            if has_det:
                g = g[g["detected"]]
            g = g.dropna(subset=["x_m", "y_m"])
            if len(g) < 25:
                continue
            x, y, k = _interp_known(g["t"].values, g["x_m"].values, g["y_m"].values, T, KNOWN_GAP_S)
            x[~k] = np.nan; y[~k] = np.nan
            pids.append(pid); teams.append(g["team"].iloc[0])
            own.append(np.stack([x, y], -1)); known.append(k); onp.append(span)
        if len(pids) < 12:
            continue
        own = np.stack(own); known = np.stack(known); onp = np.stack(onp)
        home = own.copy()
        away = np.array([tm != "Home" for tm in teams])
        home[away, :, 0] = PITCH_X_M - own[away, :, 0]
        home[away, :, 1] = PITCH_Y_M - own[away, :, 1]
        roles = {}
        for tm in ("Home", "Away"):
            idx = [i for i, t in enumerate(teams) if t == tm]
            roles.update(assign_roles(own[idx], onp[idx], [pids[i] for i in idx]))
        out[int(period)] = {"T": T, "pids": pids, "team": teams, "own": own, "home": home,
                            "known": known, "on_pitch": onp, "ball_home": np.stack([bx, by], -1),
                            "ball_known": bk, "role": roles}
    return out


def get(source, mid):
    path = cache("prep", PREP_VERSION, f"{source}_{mid}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    prep = prepare(load(source, mid))
    pd.to_pickle(prep, path)
    return prep


if __name__ == "__main__":
    import time
    import loader_metrica, loader_idsse, loader_skillcorner
    todo = ([("metrica", g) for g in loader_metrica.GAMES] + [("idsse", m) for m in loader_idsse.MATCH_IDS]
            + [("skillcorner", m) for m in loader_skillcorner.MATCH_IDS])
    if len(sys.argv) > 1:
        todo = [k for k in todo if k[0] in sys.argv[1:] or k[1] in sys.argv[1:]]
    for src, mid in todo:
        t = time.time()
        p = get(src, mid)
        print(src, mid, {k: (len(v["T"]), len(v["pids"])) for k, v in p.items()}, f"{time.time()-t:.0f}s", flush=True)
