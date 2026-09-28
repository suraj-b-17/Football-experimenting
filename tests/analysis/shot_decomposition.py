"""Decomposes the held-out shot improvement (17.1 m -> 9.4 m) into its parts, on
the SAME held-out shots as freeze_holdout.py (same matches, same seeds):

  A  old pipeline, exactly as shipped (whole-match median anchor)      [recomputed]
  B  old pipeline with ONLY the anchor bug fixed: the old RF model, old event
     ball proxy, old 1 s grid and old Kalman, but the anchor feature is a
     windowed median of the player's own anchors around t (common.features.
     player_base, the definition the new model uses) instead of the whole match
  C  new pipeline without any freeze frames
  D  new pipeline with the other shots' freeze frames as anchors (shipped)

Shots are a biased sample (attacking moments around the box).
"""
import importlib.util
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "training"))
OLD = os.path.join(ROOT, "tests", "baseline", "src_before", "pipeline")
HOLDOUT = 0.4
L, W = 105.0, 68.0


def _load(name, path, replace=()):
    src = open(path, encoding="utf-8").read()
    for a, b in replace:
        assert a in src, a
        src = src.replace(a, b)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None))
    mod.__file__ = path
    sys.modules[name] = mod
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


def old_modules(fix_anchor):
    ob = _load("old_build_match", os.path.join(OLD, "build_match.py"))
    rep = [("from build_match import", "from old_build_match import")]
    if fix_anchor:
        rep.append(("        anchor_x, anchor_y = np.median(wx), np.median(wy)\n",
                    "        from common.features import player_base\n"
                    "        _g = np.arange(track['enter'] if track['enter'] is not None else wt[0],\n"
                    "                       max(track['exit'] if track['exit'] is not None else max_t, wt[0] + 1.0), GRID_STEP_S)\n"
                    "        anchor_x, anchor_y, _, _ = player_base(np.column_stack([wt, wx, wy]), _g, 2.0)\n"))
        rep.append(("        X = np.column_stack([np.full(len(grid), anchor_x), np.full(len(grid), anchor_y),",
                    "        anchor_x = np.interp(grid, _g, anchor_x); anchor_y = np.interp(grid, _g, anchor_y)\n"
                    "        X = np.column_stack([anchor_x, anchor_y,"))
    oa = _load("old_apply_fix" if fix_anchor else "old_apply", os.path.join(OLD, "apply_to_match.py"), rep)
    return oa


def one(mid):
    from loader_statsbomb import load_match
    from apply_to_match import apply
    from build_match import role_at
    from common.features import role_from_position_name
    m = load_match(mid)
    rng = np.random.default_rng(int(mid))
    shots = [e for e in m["events"] if e["type"] == "Shot" and e.get("freezeFrame")]
    held = [e for e in shots if rng.random() < HOLDOUT]
    tracks_new, out_d, _, _ = apply(m, freeze_exclude=frozenset(e["id"] for e in held))
    _, out_c, _, _ = apply(m, use_freeze=False)
    res = {}
    for key, fix in (("A_old_shipped", False), ("B_old_anchor_fixed", True)):
        oa = old_modules(fix)
        _, hs, as_ = oa.apply(m)
        res[key] = {**hs, **as_}
    roles = {t["id"]: t["roles"] for t in tracks_new}
    rows = []
    for e in held:
        for ff in e["freezeFrame"]:
            pid = ff.get("playerId")
            if pid is None or pid == e.get("playerId") or pid not in out_d:
                continue
            x, y = (ff["x"], ff["y"]) if ff["teammate"] else (L - ff["x"], W - ff["y"])
            row = {"match": mid, "role": role_from_position_name(role_at(roles[pid], e["t"])) if roles[pid] else "MID"}
            ok = True
            for key, out in (("C_new_no_freeze", out_c), ("D_new_shipped", out_d)):
                seg = next((s for s in out[pid]["segments"] if s["t"][0] <= e["t"] <= s["t"][-1]), None)
                if seg is None:
                    ok = False; break
                row[key] = float(np.hypot(np.interp(e["t"], seg["t"], seg["x"]) - x, np.interp(e["t"], seg["t"], seg["y"]) - y))
            for key in ("A_old_shipped", "B_old_anchor_fixed"):
                s = res[key].get(str(pid))
                if s is None:
                    ok = False; break
                row[key] = float(np.hypot(np.interp(e["t"], s["t"], s["x"]) - x, np.interp(e["t"], s["t"], s["y"]) - y))
            if ok:
                rows.append(row)
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
    cols = ["A_old_shipped", "B_old_anchor_fixed", "C_new_no_freeze", "D_new_shipped"]
    df.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_decomposition.csv"), index=False)
    print(f"{df['match'].nunique()} matches, {len(df)} held-out named positions")
    print(df[cols].median().round(2).to_string())
    print(df.groupby("role")[cols].median().round(2).to_string())
