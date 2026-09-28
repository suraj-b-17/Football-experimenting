"""T3 test: does stretching the reconstructed team shape toward real team
length/width help or hurt held-out error?

For each held-out fold (out-of-fold reconstructions from train_v2), every
team's outfield positions at each second are scaled about the team centroid
by (kx, ky), with the stretch faded out near real anchors (weight =
min(1, gap / FADE_S)) so anchors still win. Reports held-out median error and
team length / width vs real. Writes training/v2_results/shape_correction.csv.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import train_v2

FADE_S = 10.0
GRID = [(1.0, 1.0), (1.0, 1.1), (1.0, 1.2), (1.0, 1.3), (1.1, 1.1), (1.1, 1.2), (1.2, 1.2), (1.2, 1.3)]


def stretch(df, ex, ey, gap, kx, ky):
    d = df[["period", "t", "team", "role"]].copy()
    d["ex"], d["ey"] = ex, ey
    of = d["role"] != 0
    g = d[of].groupby(["period", "t", "team"])
    cx = g["ex"].transform("mean"); cy = g["ey"].transform("mean")
    w = np.minimum(1.0, gap[of.values] / FADE_S)
    nx, ny = ex.copy(), ey.copy()
    nx[of.values] = cx + (d.loc[of, "ex"] - cx) * (1 + (kx - 1) * w)
    ny[of.values] = cy + (d.loc[of, "ey"] - cy) * (1 + (ky - 1) * w)
    return nx, ny


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "stage2_B"
    rows = []
    for held in train_v2.FULL:
        r = train_v2.rows_of(held); hd = r["rows"].copy(); hd["key"] = f"{held[0]}/{held[1]}"
        o = pd.read_pickle(os.path.join(train_v2.OUT, f"oof_{name}_{held[0]}_{held[1]}.pkl"))
        gap = train_v2.gap_to_anchor(hd, r["sims"])
        k = hd["known"].values
        real = train_v2.structure(hd, "true_x", "true_y")
        for kx, ky in GRID:
            nx, ny = stretch(hd, o["ex"], o["ey"], gap, kx, ky)
            e = np.hypot(nx - hd["true_x"].values, ny - hd["true_y"].values)[k]
            st = train_v2.structure(hd.assign(ex=nx, ey=ny), "ex", "ey")
            rows.append({"held": held[1], "kx": kx, "ky": ky, "median_err": float(np.median(e)),
                         "len": st["team_length_median"], "width": st["team_width_median"],
                         "back_yspread": st["back_line_yspread_median"], "real_len": real["team_length_median"],
                         "real_width": real["team_width_median"], "real_back_yspread": real["back_line_yspread_median"]})
        print(held, flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(train_v2.OUT, "shape_correction.csv"), index=False)
    print(df.groupby(["kx", "ky"]).mean(numeric_only=True).round(3).to_string())
