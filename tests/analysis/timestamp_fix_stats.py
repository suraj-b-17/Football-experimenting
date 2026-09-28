"""Season-wide: how many timestamp-reset anomalies were retimed vs dropped by
the loader's fix, and by which method."""
import os, sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)


def one(mid):
    from loader_statsbomb import load_match
    m = load_match(mid)
    return m["_timestamp_anomalies"]


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    rows = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(one, ids):
            rows += r
    print(f"{len(ids)} matches, {len(rows)} timestamp anomalies found")
    c = Counter(r["type"] for r in rows)
    for t, n in c.most_common():
        print(f"  {t}: {n}")
    retimed = [r for r in rows if r["type"] == "timestamp_reset_retimed"]
    m = Counter(r["method"] for r in retimed)
    print("retimed by method:", dict(m))
    print("matches affected:", len(set()))
