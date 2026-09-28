"""T4: season-wide counts for StatsBomb fields considered for the reconstruction."""
import os, sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)


def one(mid):
    from loader_statsbomb import load_match
    from build_match import build_tracks, NON_POSITIONAL
    m = load_match(mid)
    c = Counter()
    by_id = {e["id"]: e for e in m["events"]}
    import numpy as np
    anchors = {}
    for t in build_tracks(m):
        anchors[t["id"]] = np.array([a[:3] for a in t["anchors"]]).reshape(-1, 3)
    for e in m["events"]:
        c["events"] += 1
        c["off_camera"] += e.get("offCamera", False)
        c["under_pressure"] += e.get("underPressure", False)
        c["counterpress"] += e.get("counterpress", False)
        for f in e.get("passFlags", []):
            c["pass_" + f] += 1
        if e["type"] == "Pass" and e.get("passOutcome") == "Pass Offside":
            c["pass_offside"] += 1
        if e["type"] == "Shot":
            c["shots"] += 1
            ff = e.get("freezeFrame") or []
            c["shots_with_freeze"] += bool(ff)
            c["freeze_entries"] += len(ff)
            c["freeze_entries_no_player_id"] += sum(1 for x in ff if x.get("playerId") is None)
        for rid in e.get("relatedEvents", []):
            c["related_refs"] += 1
            r = by_id.get(rid)
            if r is None or r.get("playerId") is None or r["x"] is None or r["type"] in NON_POSITIONAL:
                continue
            A = anchors.get(r["playerId"], np.zeros((0, 3)))
            near = (np.abs(A[:, 0] - r["t"]) < 0.1) & (np.hypot(A[:, 1] - r["x"], A[:, 2] - r["y"]) < 1.0)
            if not near.any():
                c["related_new_named_location"] += 1
    return c


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    tot = Counter()
    with ProcessPoolExecutor(12) as ex:
        for c in ex.map(one, ids):
            tot.update(c)
    for k, v in sorted(tot.items()):
        print(f"{k:32s} {v:9d}  ({v / len(ids):.1f} per match)")
