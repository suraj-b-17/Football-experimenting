"""Read-only measurement: team-level structural quantities, real tracking vs
LOMO held-out reconstruction, over rolling 30s/60s windows. No fixes.

Uses the already-computed out-of-fold reconstructions from the shipped model
(training/v2_results/oof_stage2_B_<src>_<mid>.pkl, from the smoothed LOMO run)
on the 10 fully-tracked held-out matches — no refitting.

Per second, per (match, period, team), from OUTFIELD players only (role != 0)
with known=True (a real detected sample at that second, both for "real" and
for which players count toward "recon" so the two are computed on the exact
same player set):
  back_line_x    mean x of the 4 deepest (own-goal-relative) outfield players
  front_line_x   mean x of the 3 most advanced outfield players
  dist_lines     front_line_x - back_line_x
  team_length    max(x) - min(x) over outfield
  team_width     max(y) - min(y) over outfield
  lateral_com    mean y over outfield (side-to-side centre of mass)
  press_height   back_line_x, but only over seconds where the team's own
                 possession flag is 0 (out of possession)

Rolling windows: stride 5s, size 30s and 60s; a window is scored only if at
least half its seconds have >=6 known outfield players (>=3 for press_height,
computed on the out-of-poss subset only). Anchor-gap bucket uses the mean,
over outfield player-seconds in the window, of time to that player's nearest
real anchor (training/train_v2.gap_to_anchor). Possession split uses the
window's own mean possession flag (>0.5 = in possession).
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "training")); sys.path.insert(0, ROOT)
import train_v2

WINDOWS = [30, 60]
STRIDE = 5
GAP_BINS = [0, 10, 30, 60, 1e9]
GAP_LABELS = ["0-10s", "10-30s", "30-60s", "60s+"]
METRICS = ["back_line_x", "front_line_x", "dist_lines", "team_length", "team_width", "lateral_com"]


def per_second(hd, ex, ey, gap):
    d = hd[["period", "t", "team", "pid", "role", "poss", "known"]].copy()
    d["rx"], d["ry"] = hd["true_x"].values, hd["true_y"].values
    d["cx"], d["cy"] = ex, ey
    d["gap"] = gap
    of = d[(d["role"] != 0) & d["known"]]
    rows = []
    for (period, t, team), g in of.groupby(["period", "t", "team"]):
        if len(g) < 6:
            continue
        rxs, rys = g["rx"].values, g["ry"].values
        cxs, cys = g["cx"].values, g["cy"].values
        rb = np.sort(rxs)[:4].mean(); cb = np.sort(cxs)[:4].mean()
        rf = np.sort(rxs)[-3:].mean(); cf = np.sort(cxs)[-3:].mean()
        rows.append({
            "period": period, "t": t, "team": team, "poss": g["poss"].mean(), "gap": g["gap"].mean(),
            "real_back_line_x": rb, "recon_back_line_x": cb,
            "real_front_line_x": rf, "recon_front_line_x": cf,
            "real_dist_lines": rf - rb, "recon_dist_lines": cf - cb,
            "real_team_length": rxs.max() - rxs.min(), "recon_team_length": cxs.max() - cxs.min(),
            "real_team_width": rys.max() - rys.min(), "recon_team_width": cys.max() - cys.min(),
            "real_lateral_com": rys.mean(), "recon_lateral_com": cys.mean(),
            "out_of_poss": g["poss"].mean() < 0.5,
        })
    return pd.DataFrame(rows)


def rolling(ps, window):
    out = []
    for (period, team), g in ps.groupby(["period", "team"]):
        g = g.sort_values("t")
        t0, t1 = g["t"].min(), g["t"].max()
        for w0 in np.arange(t0, t1 - window, STRIDE):
            sub = g[(g["t"] >= w0) & (g["t"] < w0 + window)]
            if len(sub) < window / 2:
                continue
            row = {"period": period, "team": team, "t0": w0, "poss": sub["poss"].mean(), "gap": sub["gap"].mean(), "window": window}
            for m in METRICS:
                row["real_" + m] = sub["real_" + m].mean()
                row["recon_" + m] = sub["recon_" + m].mean()
            op = sub[sub["out_of_poss"]]
            if len(op) >= max(3, window / 10):
                row["real_press_height"] = op["real_back_line_x"].mean()
                row["recon_press_height"] = op["recon_back_line_x"].mean()
            out.append(row)
    return pd.DataFrame(out)


def stats(df, metric):
    r, c = df["real_" + metric].values, df["recon_" + metric].values
    m = ~(np.isnan(r) | np.isnan(c))
    r, c = r[m], c[m]
    if len(r) < 5:
        return {"n": len(r), "corr": np.nan, "medae": np.nan, "bias": np.nan}
    return {"n": len(r), "corr": float(np.corrcoef(r, c)[0, 1]), "medae": float(np.median(np.abs(c - r))),
            "bias": float(np.mean(c - r))}


def pairwise_rank(df, metric, n_pairs=4000, seed=0):
    d = df.dropna(subset=["real_" + metric, "recon_" + metric])
    rng = np.random.default_rng(seed)
    if len(d) < 10:
        return np.nan, 0
    i = rng.integers(0, len(d), n_pairs); j = rng.integers(0, len(d), n_pairs)
    ok = i != j
    i, j = i[ok], j[ok]
    rdiff = d["real_" + metric].values[i] - d["real_" + metric].values[j]
    cdiff = d["recon_" + metric].values[i] - d["recon_" + metric].values[j]
    valid = np.abs(rdiff) > 0.5  # meaningfully different pairs only
    agree = np.sign(rdiff[valid]) == np.sign(cdiff[valid])
    return float(agree.mean()), int(valid.sum())


if __name__ == "__main__":
    all_ps = {30: [], 60: []}
    for src, mid in train_v2.FULL:
        r = train_v2.rows_of((src, mid))
        hd, sims = r["rows"], r["sims"]
        hd = hd.reset_index(drop=True)
        oof = pd.read_pickle(os.path.join(train_v2.OUT, f"oof_stage2_B_{src}_{mid}.pkl"))
        assert oof["key"] == (src, mid)
        gap = train_v2.gap_to_anchor(hd, sims)
        ps = per_second(hd, oof["ex"], oof["ey"], gap)
        for w in WINDOWS:
            rw = rolling(ps, w)
            rw["match"] = f"{src}/{mid}"
            all_ps[w].append(rw)
        print(f"{src}/{mid} done, {len(ps)} team-seconds", flush=True)

    for w in WINDOWS:
        df = pd.concat(all_ps[w], ignore_index=True)
        df.to_csv(os.path.join(HERE, f"team_structure_{w}s.csv"), index=False)
        print(f"\n{'='*90}\nWINDOW = {w}s  ({len(df)} windows)\n{'='*90}")
        metrics_here = METRICS + (["press_height"] if "real_press_height" in df else [])
        print(f"{'metric':16s} {'n':>7s} {'corr':>7s} {'medae':>8s} {'bias':>8s}")
        for m in metrics_here:
            s = stats(df, m)
            print(f"{m:16s} {s['n']:7d} {s['corr']:7.3f} {s['medae']:8.2f} {s['bias']:+8.2f}")

        print("\n-- split by possession --")
        for label, sub in [("in_poss", df[df["poss"] > 0.5]), ("out_of_poss", df[df["poss"] <= 0.5])]:
            print(f" {label} (n={len(sub)}):")
            for m in metrics_here:
                s = stats(sub, m)
                print(f"   {m:16s} n={s['n']:6d} corr={s['corr']:7.3f} medae={s['medae']:7.2f} bias={s['bias']:+7.2f}")

        print("\n-- split by mean gap to nearest real anchor --")
        df["bucket"] = pd.cut(df["gap"], GAP_BINS, labels=GAP_LABELS, right=False)
        for b in GAP_LABELS:
            sub = df[df["bucket"] == b]
            print(f" {b} (n={len(sub)}):")
            for m in metrics_here:
                s = stats(sub, m)
                print(f"   {m:16s} n={s['n']:6d} corr={s['corr']:7.3f} medae={s['medae']:7.2f} bias={s['bias']:+7.2f}")

        print("\n-- pairwise ranking accuracy (share of pairs with |real diff|>0.5m ranked the same way) --")
        for m in ("back_line_x", "team_width"):
            acc, n = pairwise_rank(df, m)
            print(f"   {m:16s} accuracy={acc:.3f} (n_pairs={n})")
