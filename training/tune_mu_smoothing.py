"""Chooses the temporal smoothing of the model mean mu(t) by held-out error.
For each LOMO fold (10 fully tracked matches): fit the config on the other
matches, predict mu for the held-out one, smooth mu with a Gaussian of width
sigma, run the anchor Kalman, score against truth. Also reports player speed
(share of 1-s steps > 9.5 m/s) and team speed, real vs reconstruction.
Writes training/v2_results/mu_smoothing.csv."""
import argparse, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import train_v2
from common import smoother
import json

SIGMAS = [0, 1, 2, 3, 5, 8]


def smooth_by_player(df, mu, sigma):
    out = np.asarray(mu, float).copy()
    for _, idx in df.groupby(["period", "pid"]).indices.items():
        out[idx] = smoother.smooth_mean(df["t"].values[idx], out[idx], sigma)
    return out


def speed_stats(df, x, y):
    d = df.assign(ex=x, ey=y)
    d = d[d["known"]].sort_values(["period", "pid", "t"])
    same = (d["pid"].values[1:] == d["pid"].values[:-1]) & (np.diff(d["t"].values) == 1.0)
    v = np.hypot(np.diff(d["ex"].values), np.diff(d["ey"].values))[same]
    return float((v > 9.5).mean()), float(np.percentile(v, 99))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--name", default="base_B"); a = ap.parse_args()
    fs, cfg = a.name.rsplit("_", 1)
    ou = json.load(open(os.path.join(train_v2.OUT, "ou_params.json")))[a.name]
    data = {k: train_v2.rows_of(k) for k in train_v2.FULL + (train_v2.SK if cfg == "C" else [])}
    res = []
    for held in train_v2.FULL:
        hd, sims = data[held]["rows"], data[held]["sims"]
        tr = [data[k]["rows"] for k in data if k != held]
        m = train_v2.fit(tr, train_v2.FEATSETS[fs])
        mx, my = train_v2.predict(m, hd, train_v2.FEATSETS[fs])
        k = hd["known"].values
        rs = speed_stats(hd, hd["true_x"].values, hd["true_y"].values)
        res.append({"held": held[1], "sigma": "REAL", "median_err": 0, "p90_err": 0, "share_steps_gt9_5": rs[0], "step_p99": rs[1]})
        for s in SIGMAS:
            sx, sy = smooth_by_player(hd, mx, s), smooth_by_player(hd, my, s)
            ex, ey, _, _ = train_v2.reconstruct(hd, sx, sy, sims, ou, mu_sigma=0)
            e = np.hypot(ex - hd["true_x"].values, ey - hd["true_y"].values)[k]
            ss = speed_stats(hd, ex, ey)
            res.append({"held": held[1], "sigma": s, "median_err": float(np.median(e)), "p90_err": float(np.percentile(e, 90)),
                        "share_steps_gt9_5": ss[0], "step_p99": ss[1]})
        print(held, flush=True)
    df = pd.DataFrame(res)
    df.to_csv(os.path.join(train_v2.OUT, f"mu_smoothing_{a.name}.csv"), index=False)
    print(df.groupby("sigma")[["median_err", "p90_err", "share_steps_gt9_5", "step_p99"]].mean().round(4).to_string())
