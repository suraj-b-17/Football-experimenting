"""Quick sanity check: any event whose t is anomalously LARGER than its
stream-order neighbors (the dangerous direction, since it could inflate
period_offset for later periods)?"""
import os, sys
from concurrent.futures import ProcessPoolExecutor
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)


def one(mid):
    from loader_statsbomb import load_match
    m = load_match(mid)
    by_period = {}
    for e in m["events"]:
        by_period.setdefault(e["period"], []).append(e)
    out = []
    for p, evs in by_period.items():
        evs.sort(key=lambda e: e.get("index", 0))
        for i in range(1, len(evs)):
            gap = evs[i]["t"] - evs[i - 1]["t"]
            if gap > 300:  # 5 minutes within one period between consecutive-index events
                out.append({"match": mid, "period": p, "gap": round(gap, 1), "t": round(evs[i]["t"], 1),
                            "type": evs[i]["type"]})
    return out


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    rows = []
    with ProcessPoolExecutor(6) as ex:
        for r in ex.map(one, ids):
            rows += r
    print(f"{len(rows)} suspiciously large forward within-period gaps (>300s)")
    for r in rows[:20]:
        print(r)
