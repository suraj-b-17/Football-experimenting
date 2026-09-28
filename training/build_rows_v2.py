"""Sparsity-matched training rows: real tracking -> simulated StatsBomb-like
anchors and ball events (sparsity_sim) -> the SAME features production uses
(common.features.side_features, ball proxy from common.ballpath) -> target =
true position - baseline. Cached per match. Also returns what the evaluation
needs (simulated anchors per player, truth on the 1 Hz grid)."""
import json
import os
import sys
import zlib

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from paths import cache
from common import ballpath
from common.features import side_features, ROLE_CODE
import tracking_prep
import sparsity_sim

ROWS_VERSION = "r2"
PARAMS = json.load(open(os.path.join(HERE, "sparsity_params.json")))


def build_period(P, rng):
    sim = sparsity_sim.simulate(P, PARAMS, rng)
    if not sim["ball_events"]:
        return None
    path = ballpath.build_path(sim["ball_events"])
    T = P["T"]
    G = np.arange(np.ceil(T[0]), T[-1], 1.0)
    gi = np.clip(np.round((G - T[0]) * tracking_prep.HZ).astype(int), 0, len(T) - 1)
    poss_idx = ballpath.possession(sim["ball_events"], G, ["Home", "Away"])
    teams = P["team"]

    def side(team):
        d = {}
        for i, pid in enumerate(P["pids"]):
            if teams[i] != team or P["role"].get(pid) is None:
                continue
            d[pid] = {"anchors": sim["anchors"].get(pid, np.zeros((0, 3))),
                      "role": float(ROLE_CODE[P["role"][pid]]), "active": P["on_pitch"][i, gi]}
        return d

    frames = []
    for team, is_home in (("Home", True), ("Away", False)):
        own, opp = side(team), side("Away" if is_home else "Home")
        poss_own = poss_idx == (0 if is_home else 1)
        df = side_features(G, own, opp, path, poss_own, is_home)
        if df.empty:
            continue
        idx_of = {pid: i for i, pid in enumerate(P["pids"])}
        tx = np.empty(len(df)); ty = np.empty(len(df)); kn = np.empty(len(df), bool)
        gpos = {g: k for k, g in enumerate(G)}
        for pid, sub in df.groupby("pid").groups.items():
            i = idx_of[pid]
            k = gi[[gpos[g] for g in df.loc[sub, "t"].values]]
            tx[sub] = P["own"][i, k, 0]; ty[sub] = P["own"][i, k, 1]; kn[sub] = P["known"][i, k]
        df["true_x"], df["true_y"], df["known"] = tx, ty, kn
        df["dx"], df["dy"] = tx - df["base_x"].values, ty - df["base_y"].values
        df["team"] = team
        frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True), sim


def get(source, mid):
    path = cache("rows", ROWS_VERSION, f"{source}_{mid}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    prep = tracking_prep.get(source, mid)
    rng = np.random.default_rng(zlib.crc32(f"{source}/{mid}".encode()))
    rows, sims = [], {}
    for period, P in prep.items():
        r = build_period(P, rng)
        if r is None:
            continue
        df, sim = r
        df["period"] = period
        rows.append(df)
        sims[period] = {"anchors": sim["anchors"], "ball_events": sim["ball_events"]}
    out = {"rows": pd.concat(rows, ignore_index=True), "sims": sims}
    for c in out["rows"].columns:
        if out["rows"][c].dtype == np.float64:
            out["rows"][c] = out["rows"][c].astype(np.float32)
    pd.to_pickle(out, path)
    return out


def _one(k):
    import time
    t = time.time(); r = get(*k)
    return k, len(r["rows"]), time.time() - t


if __name__ == "__main__":
    from concurrent.futures import ProcessPoolExecutor
    import loader_metrica, loader_idsse, loader_skillcorner
    todo = ([("metrica", g) for g in loader_metrica.GAMES] + [("idsse", m) for m in loader_idsse.MATCH_IDS]
            + [("skillcorner", m) for m in loader_skillcorner.MATCH_IDS])
    if len(sys.argv) > 1:
        todo = [k for k in todo if k[0] in sys.argv[1:] or k[1] in sys.argv[1:]]

    with ProcessPoolExecutor(max_workers=8) as ex:
        for k, n, dt in ex.map(_one, todo):
            print(k, n, "rows", f"{dt:.0f}s", flush=True)
