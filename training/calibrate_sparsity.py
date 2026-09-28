"""Grid-searches sparsity_sim parameters so simulated per-role anchor gaps and
anchors/90 match the real season (real_sparsity.json). Writes
training/sparsity_params.json and training/sparsity_compare.csv (side by side)."""
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import tracking_prep
import sparsity_sim

CAL_MATCHES = [("metrica", "Sample_Game_1"), ("metrica", "Sample_Game_3"), ("idsse", "J03WPY"),
               ("idsse", "J03WMX"), ("idsse", "J03WN1")]
ROLES = ("GK", "DEF", "MID", "FWD")


def stats(periods, params, seed=0):
    rng = np.random.default_rng(seed)
    pairs = [(sparsity_sim.simulate(P, params, rng), P) for P in periods]
    gaps, per90 = sparsity_sim.gap_stats(pairs)
    out = {}
    for r in ROLES:
        g = np.array(gaps.get(r, [1.0]))
        out[r] = {"median": float(np.median(g)), "p75": float(np.percentile(g, 75)), "p90": float(np.percentile(g, 90)),
                  "p99": float(np.percentile(g, 99)), "max": float(g.max()),
                  "anchors_per90_median": float(np.median(per90.get(r, [0]))),
                  "cdf": {str(x): float((g < x).mean()) for x in CDF_AT}}
    return out


CDF_AT = (1, 2, 5, 10, 30, 60, 120, 300)


def loss(sim, real):
    """Squared CDF differences at CDF_AT (x10) + squared log anchor-rate ratio, per role."""
    l = 0.0
    for r in ROLES:
        for x in CDF_AT:
            l += 10 * (sim[r]["cdf"][str(x)] - real[r]["cdf"][str(x)]) ** 2
        l += np.log(max(sim[r]["anchors_per90_median"], 1e-3) / real[r]["anchors_per90_median"]) ** 2
    return l


if __name__ == "__main__":
    real = json.load(open(os.path.join(HERE, "real_sparsity.json")))
    rg = np.load(os.path.join(HERE, "real_gaps_sample.npz"))
    for r in ROLES:
        g = rg[r][rg[r] > 0.05]
        real[r]["cdf"] = {str(x): float((g < x).mean()) for x in CDF_AT}
    periods = [P for src, mid in CAL_MATCHES for P in tracking_prep.get(src, mid).values()]
    grid = {"r_ctrl": [1.0, 1.5, 2.0, 2.5], "p_press": [0.2, 0.4, 0.6, 0.8], "p_press2": [0.0, 0.2, 0.4],
            "min_spell_s": [0.2, 0.6], "r_press": [4.0, 6.0], "merge_gap_s": [0.4, 1.0, 2.0]}
    best = None
    for vals in itertools.product(*grid.values()):
        prm = dict(zip(grid.keys(), vals))
        s = stats(periods, prm)
        l = loss(s, real)
        if best is None or l < best[0]:
            best = (l, prm, s)
            print(f"loss {l:.3f} {prm}", flush=True)
    l, prm, s = best
    json.dump(prm, open(os.path.join(HERE, "sparsity_params.json"), "w"), indent=1)
    rows = []
    for r in ROLES:
        for k in ("median", "p75", "p90", "p99", "max", "anchors_per90_median"):
            rows.append({"role": r, "stat": k, "real_statsbomb": round(real[r][k], 2), "simulated": round(s[r][k], 2)})
        for x in CDF_AT:
            rows.append({"role": r, "stat": f"share<{x}s", "real_statsbomb": round(real[r]["cdf"][str(x)], 3),
                         "simulated": round(s[r]["cdf"][str(x)], 3)})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(HERE, "sparsity_compare.csv"), index=False)
    print(df.pivot(index="stat", columns="role", values=["real_statsbomb", "simulated"]).round(1).to_string())
