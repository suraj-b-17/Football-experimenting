"""T4: shot freeze frames as a held-out check of the StatsBomb reconstruction.

For a random sample of matches, 40 % of shots (seeded) are held out: the match
is reconstructed WITHOUT their freeze frames (but with every other anchor,
including the other shots' freeze frames), and the reconstruction at each
held-out shot's timestamp is compared with the real named positions in that
freeze frame (opponents converted into their own frame). Shots are a biased
sample (attacking moments, players around the box), so this is a check on
those moments, not a season-wide error. The shooter is excluded (his own shot
location is an anchor at that time).
Writes tests/analysis/freeze_holdout.csv and prints error by role.
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)
HOLDOUT = 0.4
PITCH_X_M, PITCH_Y_M = 105.0, 68.0


def one(mid):
    from loader_statsbomb import load_match
    from apply_to_match import apply
    from build_match import role_at
    from common.features import role_from_position_name
    m = load_match(mid)
    rng = np.random.default_rng(int(mid))
    shots = [e for e in m["events"] if e["type"] == "Shot" and e.get("freezeFrame")]
    held = {e["id"] for e in shots if rng.random() < HOLDOUT}
    tracks, out, _, _ = apply(m, freeze_exclude=frozenset(held))
    _, out_nf, _, _ = apply(m, use_freeze=False)
    by_id = {t["id"]: t for t in tracks}
    import json
    old_path = os.path.join(os.path.expanduser("~"), ".cache", "football_anim", "before_payloads", f"{mid}.json")
    old = {}
    if os.path.exists(old_path):
        od = json.load(open(old_path, encoding="utf-8"))
        for side in ("home", "away"):
            for t in od[side]["tracks"]:
                if t.get("smoothed"):
                    old[t["id"]] = t["smoothed"]
    rows = []
    for e in shots:
        if e["id"] not in held:
            continue
        for ff in e["freezeFrame"]:
            pid = ff.get("playerId")
            if pid is None or pid not in out or pid == e.get("playerId"):
                continue
            x, y = (ff["x"], ff["y"]) if ff["teammate"] else (PITCH_X_M - ff["x"], PITCH_Y_M - ff["y"])
            seg = next((s for s in out[pid]["segments"] if s["t"][0] <= e["t"] <= s["t"][-1]), None)
            if seg is None:
                continue
            ex, ey = np.interp(e["t"], seg["t"], seg["x"]), np.interp(e["t"], seg["t"], seg["y"])
            tr = by_id[pid]
            role = role_from_position_name(role_at(tr["roles"], e["t"])) if tr["roles"] else "MID"
            A = np.array([a[0] for a in tr["anchors"]])
            gap = float(np.min(np.abs(A - e["t"]))) if len(A) else 1e9
            sn = next((q for q in out_nf[pid]["segments"] if q["t"][0] <= e["t"] <= q["t"][-1]), None)
            err_nf = float(np.hypot(np.interp(e["t"], sn["t"], sn["x"]) - x, np.interp(e["t"], sn["t"], sn["y"]) - y)) if sn else np.nan
            o = old.get(pid)
            err_old = float(np.hypot(np.interp(e["t"], o["t"], o["x"]) - x, np.interp(e["t"], o["t"], o["y"]) - y)) if o else np.nan
            rows.append({"match": mid, "shot": e["id"], "pid": pid, "role": role, "teammate": ff["teammate"],
                         "err_m": float(np.hypot(ex - x, ey - y)), "err_old_m": err_old, "err_nofreeze_m": err_nf, "gap_to_anchor_s": gap})
    return rows


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    ids = list(np.random.default_rng(0).choice(ids, size=n, replace=False))
    rows = []
    with ProcessPoolExecutor(4) as ex:
        for r in ex.map(one, ids):
            rows += r
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "freeze_holdout.csv"), index=False)
    print(f"{df['match'].nunique()} matches, {df['shot'].nunique()} held-out shots, {len(df)} named player positions")
    print(df.groupby("role")["err_m"].describe(percentiles=[0.5, 0.9]).round(2).to_string())
    print("new vs OLD shipped reconstruction, median by role:")
    print(df.groupby("role")[["err_m", "err_nofreeze_m", "err_old_m"]].median().round(2).to_string())
    print("overall: new %.2f m, new without any freeze frames %.2f m, old %.2f m" % (
        df["err_m"].median(), df["err_nofreeze_m"].median(), df["err_old_m"].median()))
    print("by side:", df.groupby("teammate")["err_m"].median().round(2).to_dict(), "(True = shooter's team)")
    df["bucket"] = pd.cut(df["gap_to_anchor_s"], [0, 5, 10, 20, 30, 60, 120, 1e9], right=False,
                          labels=["0-5s", "5-10s", "10-20s", "20-30s", "30-60s", "60-120s", "120s+"])
    print(df.groupby("bucket")["err_m"].agg(["median", "size"]).round(2).to_string())
    print("overall median %.2f m" % df["err_m"].median())
