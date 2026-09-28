"""The single feature definition shared by training, benchmark and production.

Everything is in the player's OWN frame (own goal at x=0, 105x68 m). Inputs are
only what a StatsBomb-style event feed provides (or what the sparsity simulator
produces from real tracking): each player's own sparse anchors, the ball-proxy
path built by common.ballpath, possession, role, and the other players' sparse
anchors. Nothing is a whole-match constant: the player's baseline is a median of
their OWN anchors inside a window around t (widened only when the window is
empty), and a replay is offline, so anchors after t may be used.

Target (training only): true position - baseline.
"""
import numpy as np
import pandas as pd

from common import ballpath

PITCH_X_M, PITCH_Y_M = 105.0, 68.0
ROLE_CODE = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
ROLE_DEFAULT_X = {0: 5.0, 1: 25.0, 2: 45.0, 3: 65.0}
WINDOWS_S = (60.0, 180.0, 600.0)
DT_CAP_S = 900.0
MATE_WINDOW_S = 60.0

FEATS_BASE = [
    "role", "base_w", "n60", "dtp", "dtn", "pdx", "pdy", "ndx", "ndy", "idx", "idy",
    "bx", "by", "brx", "bry", "bvx", "bvy", "poss",
]
FEATS_COUPLED = FEATS_BASE + [
    "t_deep", "t_med", "t_adv", "t_n", "o_line", "o_front", "o_n", "rank_x",
]
FEATS_STAGE2 = FEATS_COUPLED + ["s2_own_line", "s2_own_front", "s2_opp_line", "s2_rank"]
TARGETS = ["dx", "dy"]


def role_from_position_name(name):
    n = (name or "").lower()
    if "goalkeeper" in n:
        return "GK"
    if "back" in n:
        return "DEF"
    if "midfield" in n:
        return "MID"
    return "FWD"


def _window_median(A, V, G, half):
    """Median of V[A in [g-half, g+half]] for each g; NaN where empty. Memoised on the slice."""
    lo = np.searchsorted(A, G - half, side="left")
    hi = np.searchsorted(A, G + half, side="right")
    out = np.full(len(G), np.nan)
    cnt = hi - lo
    cache = {}
    for i in np.nonzero(cnt > 0)[0]:
        key = (lo[i], hi[i])
        if key not in cache:
            cache[key] = np.median(V[lo[i]:hi[i]])
        out[i] = cache[key]
    return out, cnt


def player_base(anchors, G, role_code):
    """Windowed-median baseline of a player's own anchors. Returns base_x, base_y, window_used, n60."""
    bx = np.full(len(G), np.nan)
    by = np.full(len(G), np.nan)
    w = np.full(len(G), np.nan)
    n60 = np.zeros(len(G))
    if len(anchors):
        A, AX, AY = anchors[:, 0], anchors[:, 1], anchors[:, 2]
        for half in WINDOWS_S:
            mx, cnt = _window_median(A, AX, G, half)
            my, _ = _window_median(A, AY, G, half)
            if half == WINDOWS_S[0]:
                n60 = cnt.astype(float)
            fill = np.isnan(bx) & ~np.isnan(mx)
            bx[fill], by[fill], w[fill] = mx[fill], my[fill], half
    return bx, by, w, n60


def prev_next(anchors, G):
    n = len(G)
    if len(anchors) == 0:
        z = np.zeros(n)
        return np.full(n, DT_CAP_S), np.full(n, DT_CAP_S), z * np.nan, z * np.nan, z * np.nan, z * np.nan
    A = anchors[:, 0]
    ip = np.searchsorted(A, G, side="right") - 1
    inx = np.searchsorted(A, G, side="left")
    has_p = ip >= 0
    has_n = inx < len(A)
    ipc, inc = np.clip(ip, 0, len(A) - 1), np.clip(inx, 0, len(A) - 1)
    dtp = np.where(has_p, G - A[ipc], DT_CAP_S)
    dtn = np.where(has_n, A[inc] - G, DT_CAP_S)
    px = np.where(has_p, anchors[ipc, 1], np.nan)
    py = np.where(has_p, anchors[ipc, 2], np.nan)
    nx = np.where(has_n, anchors[inc, 1], np.nan)
    ny = np.where(has_n, anchors[inc, 2], np.nan)
    return np.minimum(dtp, DT_CAP_S), np.minimum(dtn, DT_CAP_S), px, py, nx, ny


def side_features(G, players, opp_players, path, poss_is_own, is_home):
    """One team, one period.

    G: grid times (1-D). players / opp_players: {pid: {"anchors": (n,3) own-frame
    hard anchors sorted by t, "role": role code array over G (or scalar),
    "active": bool array over G}}. path: ballpath knots (home frame).
    poss_is_own: bool array over G. Returns a DataFrame with one row per active
    (player, grid time): t, pid, base_x, base_y, and FEATS_COUPLED columns.
    """
    bxh, byh = ballpath.sample(path, G)
    bvxh, bvyh = ballpath.velocity(path, G)
    ball_x, ball_y = ballpath.to_own_frame(bxh, byh, is_home)
    ball_vx, ball_vy = (bvxh, bvyh) if is_home else (-bvxh, -bvyh)

    def bases(pl):
        out = {}
        for pid, p in pl.items():
            role = np.broadcast_to(np.asarray(p["role"], dtype=float), G.shape)
            bx, by, w, n60 = player_base(p["anchors"], G, role)
            out[pid] = (bx, by, w, n60, role)
        return out

    own_b = bases(players)
    opp_b = bases(opp_players)

    def fill_fallback(bmap, pl):
        # empty in every window: teammates' same-role median, then a role default
        for pid, (bx, by, w, n60, role) in bmap.items():
            miss = np.isnan(bx)
            if not miss.any():
                continue
            same = [bmap[q] for q in bmap if q != pid]
            for i in np.nonzero(miss)[0]:
                vals = [(s[0][i], s[1][i]) for s in same if s[4][i] == role[i] and not np.isnan(s[0][i])]
                if vals:
                    bx[i] = np.median([v[0] for v in vals]); by[i] = np.median([v[1] for v in vals])
                else:
                    bx[i] = ROLE_DEFAULT_X[int(role[i])]; by[i] = PITCH_Y_M / 2
                w[i] = 9999.0
    fill_fallback(own_b, players)
    fill_fallback(opp_b, opp_players)

    def outfield_matrix(bmap, pl, convert):
        rows = []
        for pid, (bx, by, w, n60, role) in bmap.items():
            v = np.where(pl[pid]["active"] & (role != 0), bx, np.nan)
            rows.append(PITCH_X_M - v if convert else v)
        return np.vstack(rows) if rows else np.full((1, len(G)), np.nan)

    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            M = outfield_matrix(own_b, players, False)
            t_deep, t_med, t_adv = np.nanmin(M, 0), np.nanmedian(M, 0), np.nanmax(M, 0)
            t_n = np.sum(~np.isnan(M), 0)
            O = outfield_matrix(opp_b, opp_players, True)
            o_line, o_front = np.nanmax(O, 0), np.nanmin(O, 0)
            o_n = np.sum(~np.isnan(O), 0)

    frames = []
    for pid, p in players.items():
        act = p["active"]
        if not act.any():
            continue
        bx, by, w, n60, role = own_b[pid]
        dtp, dtn, px, py, nx, ny = prev_next(p["anchors"], G)
        # time-linear interpolation between the bracketing anchors
        tot = dtp + dtn
        f = np.where(tot > 0, dtp / np.where(tot > 0, tot, 1), 0.0)
        ix = np.where(np.isnan(px), nx, np.where(np.isnan(nx), px, px + (nx - px) * f))
        iy = np.where(np.isnan(py), ny, np.where(np.isnan(ny), py, py + (ny - py) * f))
        with np.errstate(all="ignore"):
            rank = np.sum(M <= bx[None, :], axis=0) / np.maximum(np.sum(~np.isnan(M), axis=0), 1)
        d = pd.DataFrame({
            "t": G, "pid": pid, "base_x": bx, "base_y": by,
            "role": role, "base_w": w, "n60": n60, "dtp": dtp, "dtn": dtn,
            "pdx": np.nan_to_num(px - bx), "pdy": np.nan_to_num(py - by),
            "ndx": np.nan_to_num(nx - bx), "ndy": np.nan_to_num(ny - by),
            "idx": np.nan_to_num(ix - bx), "idy": np.nan_to_num(iy - by),
            "bx": ball_x, "by": ball_y, "brx": ball_x - bx, "bry": ball_y - by,
            "bvx": ball_vx, "bvy": ball_vy, "poss": poss_is_own.astype(float),
            "t_deep": np.nan_to_num(t_deep - bx), "t_med": np.nan_to_num(t_med - bx),
            "t_adv": np.nan_to_num(t_adv - bx), "t_n": t_n,
            "o_line": np.nan_to_num(o_line - bx), "o_front": np.nan_to_num(o_front - bx), "o_n": o_n,
            "rank_x": np.nan_to_num(rank),
        })
        frames.append(d[act])
    if not frames:
        return pd.DataFrame(columns=["t", "pid", "base_x", "base_y"] + FEATS_COUPLED)
    return pd.concat(frames, ignore_index=True)


def stage2_features(df_own, df_opp, mu_own_x, mu_opp_x):
    """Adds estimated line heights from stage-1 predictions of every player on
    both teams at the same t. df_own/df_opp are side_features() outputs for the
    two teams of the same period; mu_*_x are stage-1 predicted own-frame x."""
    a = df_own[["t", "pid", "role"]].copy(); a["mx"] = mu_own_x
    b = df_opp[["t", "pid", "role"]].copy(); b["mx"] = mu_opp_x
    ao, bo = a[a["role"] != 0], b[b["role"] != 0]
    own_line = ao.groupby("t")["mx"].min()
    own_front = ao.groupby("t")["mx"].max()
    opp_line = PITCH_X_M - bo.groupby("t")["mx"].min()
    out = df_own.copy()
    out["s2_own_line"] = out["t"].map(own_line).fillna(0).values - out["base_x"].values
    out["s2_own_front"] = out["t"].map(own_front).fillna(0).values - out["base_x"].values
    out["s2_opp_line"] = out["t"].map(opp_line).fillna(PITCH_X_M).values - out["base_x"].values
    a["rk"] = a.groupby("t")["mx"].rank(pct=True)
    out["s2_rank"] = a["rk"].values
    return out
