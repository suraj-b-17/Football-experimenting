"""T1d: do Tactical Shift position-label changes show up in real anchors?"""
import os, sys
import numpy as np
from concurrent.futures import ProcessPoolExecutor
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)


def one(mid):
    from loader_statsbomb import load_match
    from build_match import build_tracks
    m = load_match(mid)
    tracks = {t["id"]: t for t in build_tracks(m, use_freeze=False)}
    lineups, rows, shifts = {}, [], 0
    for e in sorted(m["events"], key=lambda e: e["t"]):
        if e["type"] == "Starting XI":
            lineups[e["teamId"]] = {p["playerId"]: p["positionName"] for p in e["tacticsLineup"]}
        elif e["type"] == "Tactical Shift":
            shifts += 1
            prev = lineups.get(e["teamId"], {})
            new = {p["playerId"]: p["positionName"] for p in e["tacticsLineup"]}
            for pid, pos in new.items():
                if pid not in prev or pid not in tracks:
                    continue
                A = np.array([a[:3] for a in tracks[pid]["anchors"] if a[3] == "event"]).reshape(-1, 3)
                b = A[(A[:, 0] >= e["t"] - 600) & (A[:, 0] < e["t"])]
                a = A[(A[:, 0] > e["t"]) & (A[:, 0] <= e["t"] + 600)]
                if len(b) >= 5 and len(a) >= 5:
                    d = np.hypot(np.median(a[:, 1]) - np.median(b[:, 1]), np.median(a[:, 2]) - np.median(b[:, 2]))
                    rows.append((prev[pid] != pos, d))
            lineups[e["teamId"]] = new
    return shifts, rows


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    S, R = 0, []
    with ProcessPoolExecutor(12) as ex:
        for s, r in ex.map(one, ids):
            S += s; R += r
    R = np.array(R, dtype=float)
    ch, un = R[R[:, 0] == 1, 1], R[R[:, 0] == 0, 1]
    print(f"Tactical Shift events: {S}; player-shift pairs with >=5 anchors in the 10 min either side: {len(R)}")
    print(f"  label CHANGED:   n={len(ch)}, move of own-anchor median: median {np.median(ch):.1f} m, mean {ch.mean():.1f}, p75 {np.percentile(ch, 75):.1f}")
    print(f"  label UNCHANGED: n={len(un)}, median {np.median(un):.1f} m, mean {un.mean():.1f}, p75 {np.percentile(un, 75):.1f}")
