"""Read-only scan: how often does a StatsBomb event's declared time disagree
with its stream position? For each match, events sorted by `index` should
have non-decreasing `t` (continuous match clock); report every case where it
decreases by more than a few seconds, since a real jump backward in time
inside a single, continuous StatsBomb-ordered index stream is a source data
quirk, not real chronology.
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)


def one(mid):
    from loader_statsbomb import load_match
    m = load_match(mid)
    evs = sorted((e for e in m["events"] if e.get("index") is not None), key=lambda e: e["index"])
    out = []
    last_t, last_idx = -1, None
    for e in evs:
        if e["t"] < last_t - 2.0:
            out.append({"match": mid, "index": e["index"], "prev_index": last_idx, "t": round(e["t"], 3),
                        "prev_t": round(last_t, 3), "type": e["type"], "has_loc": e["x"] is not None,
                        "playerId": e.get("playerId")})
        last_t, last_idx = e["t"], e["index"]
    return out


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    if len(sys.argv) > 1:
        ids = ids[: int(sys.argv[1])]
    rows = []
    with ProcessPoolExecutor(6) as ex:
        for r in ex.map(one, ids):
            rows += r
    print(f"{len(ids)} matches scanned, {len(rows)} events where t jumps backward >2s vs stream order")
    with_loc = [r for r in rows if r["has_loc"]]
    print(f"of which have a location (could become a bad anchor): {len(with_loc)}")
    for r in rows[:30]:
        print(r)
