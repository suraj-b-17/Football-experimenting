"""Season-wide scan, every event type: how often does an event's time go
backward relative to the previous event by stream index (within the same
period)? This is the RAW signal (before the loader's own retime/drop fix),
read from the raw cached JSON directly so it reflects the untouched source
data. Reports counts by event type and period."""
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "football_anim", "statsbomb", "events")


def hms(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def one(mid):
    raw = json.load(open(os.path.join(CACHE, f"{mid}.json"), encoding="utf-8"))
    by_period = {}
    for e in raw:
        by_period.setdefault(e["period"], []).append(e)
    out = []
    for p, evs in by_period.items():
        evs.sort(key=lambda e: e["index"])
        for i in range(1, len(evs)):
            t_i, t_p = hms(evs[i]["timestamp"]), hms(evs[i - 1]["timestamp"])
            if t_i < t_p - 2.0:
                out.append({"match": mid, "period": p, "type": evs[i]["type"]["name"],
                            "has_loc": evs[i].get("location") is not None,
                            "gap": round(t_p - t_i, 2)})
    return out


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    rows = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(one, ids):
            rows += r
    print(f"{len(ids)} matches, {len(rows)} events with t going backward >2s vs previous event by index\n")
    by_type = Counter(r["type"] for r in rows)
    print("by event type:")
    for t, n in by_type.most_common():
        print(f"  {t:20s} {n:4d}")
    by_period = Counter(r["period"] for r in rows)
    print("\nby period:")
    for p, n in sorted(by_period.items()):
        print(f"  period {p}: {n}")
    print(f"\nwith a location (concerns anchors): {sum(1 for r in rows if r['has_loc'])}")
    print(f"matches affected: {len(set(r['match'] for r in rows))} / {len(ids)}")
