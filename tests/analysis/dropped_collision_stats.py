import os, sys
import numpy as np
from concurrent.futures import ProcessPoolExecutor
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)


def one(mid):
    from loader_statsbomb import load_match
    from build_match import build_tracks
    m = load_match(mid)
    build_tracks(m)
    return [(c["dist_m"], c["kind"]) for c in m.get("_collisions", []) if c["type"] == "collision_dropped"]


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    rows = []
    with ProcessPoolExecutor(12) as ex:
        for r in ex.map(one, ids):
            rows += r
    d = np.array([r[0] for r in rows])
    print("dropped:", len(d), "dist median %.3f p90 %.3f max %.3f, count <=0.1m: %d, 0.1-0.5m: %d" % (
        np.median(d), np.percentile(d, 90), d.max(), (d <= 0.1).sum(), ((d > 0.1) & (d <= 0.5)).sum()))
    from collections import Counter
    print("kinds among >0.1m drops:", Counter(r[1] for r in rows if r[0] > 0.1))
