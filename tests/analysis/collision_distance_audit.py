"""Audit every collision-resolution merge/retime/drop season-wide: full
distance distribution, sanity-check that 'true duplicate' never exceeds
SAME_PLACE_M, and full detail on the single largest-distance case."""
import os, sys
from concurrent.futures import ProcessPoolExecutor
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)
import build_match as BM


def one(mid):
    from loader_statsbomb import load_match
    m = load_match(mid)
    BM.build_tracks(m)
    out = []
    for c in m.get("_collisions", []):
        if c["type"] in ("collision_retimed", "collision_duplicate_dropped", "collision_dropped"):
            out.append({"match": mid, **c})
    return out


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    rows = []
    with ProcessPoolExecutor(12) as ex:
        for r in ex.map(one, ids):
            rows += r
    import pandas as pd
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "collision_distance_audit.csv"), index=False)

    print(f"total logged collision events: {len(df)}")
    print("\nby type, distance stats (m):")
    print(df.groupby("type")["dist_m"].agg(["count", "min", "median", "max"]).to_string())

    dup = df[df["type"] == "collision_duplicate_dropped"]
    bad_dup = dup[dup["dist_m"] > 0.05 + 1e-6]
    print(f"\n'true duplicate' entries with dist_m > 0.05 (should be ZERO): {len(bad_dup)}")
    if len(bad_dup):
        print(bad_dup.sort_values("dist_m", ascending=False).head(20).to_string())

    over1 = df[df["dist_m"] > 1.0]
    over5 = df[df["dist_m"] > 5.0]
    print(f"\nmerges (any type) with dist_m > 1m: {len(over1)} ({100*len(over1)/len(df):.3f}%)")
    print(f"merges (any type) with dist_m > 5m: {len(over5)} ({100*len(over5)/len(df):.3f}%)")
    print("\nby type, among >1m:")
    print(over1["type"].value_counts().to_string())
    print("\nby type, among >5m:")
    print(over5["type"].value_counts().to_string())

    worst = df.sort_values("dist_m", ascending=False).iloc[0]
    print("\nworst single case:")
    print(worst.to_string())
