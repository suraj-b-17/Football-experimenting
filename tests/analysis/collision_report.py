"""T7 anchors report: season-wide collision statistics under the CURRENT
(post-fix) two-pass resolve_collisions, plus a like-for-like comparison
against the OLD single-pass/0.5m/kind-unaware logic, to quantify how many
carry end points (and other anchors) the old logic silently dropped without
even fitting them elsewhere.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)
import build_match as BM

MERGE_S = BM.MERGE_S
MAX_SPEED_MPS = BM.MAX_SPEED_MPS


def old_resolve_collisions(pts):
    """The pre-fix logic: single pass, 0.5m same-place threshold, ANY kind,
    no pre-dedup — reproduced exactly for comparison, not used in production."""
    OLD_SAME_PLACE_M = 0.5
    out, dropped_no_fit, dropped_dup = [], [], []
    for i, a in enumerate(pts):
        if out:
            dt = a[0] - out[-1][0]
            d = ((a[1] - out[-1][1]) ** 2 + (a[2] - out[-1][2]) ** 2) ** 0.5
            implied = d / max(dt, 1e-6)
        if out and (dt <= MERGE_S or implied > MAX_SPEED_MPS):
            prev = out[-1]
            if d <= OLD_SAME_PLACE_M:
                dropped_dup.append((a, d, dt))
                continue
            t_new = prev[0] + max(d / MAX_SPEED_MPS, MERGE_S + 0.001)
            nxt = pts[i + 1][0] if i + 1 < len(pts) else float("inf")
            if t_new < nxt - MERGE_S:
                out.append((t_new, a[1], a[2], a[3], a[4]))
            else:
                dropped_no_fit.append((a, d, dt))
            continue
        out.append(a)
    return out, dropped_dup, dropped_no_fit


def raw_points_per_player(match):
    """Re-derive the pre-collision-resolution anchor list per player, same
    construction as build_tracks but without calling resolve_collisions."""
    anchors = {}

    def add(pid, t, x, y, kind, order):
        anchors.setdefault(pid, []).append((t, x, y, kind, order))

    own_event_ts = {}
    for e in match["events"]:
        pid = e.get("playerId")
        if pid is None or e["type"] in BM.NON_POSITIONAL or e["x"] is None:
            continue
        kind = "event_offcam" if e.get("offCamera") else "event"
        add(pid, e["t"], e["x"], e["y"], kind, e["index"])
        own_event_ts.setdefault(pid, []).append(e["t"])
        if e.get("carryEnd"):
            add(pid, e["t"] + e["duration"], e["carryEnd"][0], e["carryEnd"][1], "carry_end", e["index"] + 0.5)
            own_event_ts[pid].append(e["t"] + e["duration"])
    for v in own_event_ts.values():
        v.sort()
    for e in match["events"]:
        rid = e.get("passRecipientId")
        if e["type"] != "Pass" or e.get("passOutcome") != "Complete" or rid is None or not e.get("passEnd"):
            continue
        t_arr = e["t"] + e["duration"]
        import bisect
        ts = own_event_ts.get(rid, [])
        i = bisect.bisect_left(ts, t_arr - 0.5)
        if not (i < len(ts) and ts[i] <= t_arr + 0.5):
            add(rid, t_arr, e["passEnd"][0], e["passEnd"][1], "reception", e["index"] + 0.6)
    for e in match["events"]:
        if e["type"] != "Shot":
            continue
        for ff in e.get("freezeFrame") or []:
            pid = ff.get("playerId")
            if pid is None:
                continue
            x, y = (ff["x"], ff["y"]) if ff["teammate"] else (105.0 - ff["x"], 68.0 - ff["y"])
            add(pid, e["t"], x, y, "freeze", e["index"] + 0.7)
    return {pid: sorted(lst, key=lambda a: (round(a[0], 3), a[4])) for pid, lst in anchors.items()}


def one(mid):
    from loader_statsbomb import load_match
    m = load_match(mid)
    raw = raw_points_per_player(m)
    total_raw = sum(len(v) for v in raw.values())
    new_log = []
    new_kept = 0
    old_dup_dropped, old_nofit_dropped = 0, 0
    old_carry_end_lost = 0
    for pid, pts in raw.items():
        kept = BM.resolve_collisions(pid, pts, new_log)
        new_kept += len(kept)
        _, dup, nofit = old_resolve_collisions(pts)
        old_dup_dropped += len(dup)
        old_nofit_dropped += len(nofit)
        old_carry_end_lost += sum(1 for a, d, dt in dup if a[3] == "carry_end")
        old_carry_end_lost += sum(1 for a, d, dt in nofit if a[3] == "carry_end")
    retimed = [c for c in new_log if c["type"] == "collision_retimed"]
    dup_dropped = [c for c in new_log if c["type"] == "collision_duplicate_dropped"]
    nofit_dropped = [c for c in new_log if c["type"] == "collision_dropped"]
    return {
        "total_raw": total_raw, "new_kept": new_kept,
        "new_retimed": len(retimed), "new_dup_dropped": len(dup_dropped), "new_nofit_dropped": len(nofit_dropped),
        "retimed_shifts": [c["t_new"] - c["t"] for c in retimed],
        "merged_dists": [c["dist_m"] for c in (retimed + dup_dropped + nofit_dropped)],
        "old_dup_dropped": old_dup_dropped, "old_nofit_dropped": old_nofit_dropped,
        "old_carry_end_lost": old_carry_end_lost,
        "new_carry_end_raw": sum(1 for pts in raw.values() for a in pts if a[3] == "carry_end"),
        "new_carry_end_dup_dropped": sum(1 for c in dup_dropped if c["kind"] == "carry_end"),
        "new_carry_end_nofit_dropped": sum(1 for c in nofit_dropped if c["kind"] == "carry_end"),
    }


if __name__ == "__main__":
    from run_season import all_match_ids
    ids = [m for m, _ in all_match_ids(2, 27)]
    if len(sys.argv) > 1:
        ids = ids[: int(sys.argv[1])]
    agg = {}
    shifts, dists = [], []
    for i, mid in enumerate(ids):
        r = one(mid)
        for k, v in r.items():
            if k in ("retimed_shifts", "merged_dists"):
                continue
            agg[k] = agg.get(k, 0) + v
        shifts += r["retimed_shifts"]
        dists += r["merged_dists"]
        if (i + 1) % 50 == 0:
            print(f"...{i+1}/{len(ids)}", flush=True)
    shifts, dists = np.array(shifts), np.array(dists)
    total_anchors = agg["total_raw"]
    print(f"\n{len(ids)} matches, {total_anchors} raw anchor observations before collision resolution")
    print(f"kept (current, fixed pipeline): {agg['new_kept']} ({100*agg['new_kept']/total_anchors:.2f}%)")
    print(f"re-timed: {agg['new_retimed']} ({100*agg['new_retimed']/total_anchors:.3f}%), "
          f"largest time shift: {shifts.max():.3f}s, median {np.median(shifts):.3f}s")
    print(f"dropped as true duplicate (<=0.05m, same instant): {agg['new_dup_dropped']} "
          f"({100*agg['new_dup_dropped']/total_anchors:.3f}%)")
    print(f"dropped, no room to fit: {agg['new_nofit_dropped']} ({100*agg['new_nofit_dropped']/total_anchors:.3f}%)")
    print(f"largest distance among any merged/retimed/dropped pair: {dists.max():.2f}m, median {np.median(dists):.2f}m")
    tot_ce = agg["new_carry_end_raw"]
    dup_ce, nofit_ce = agg["new_carry_end_dup_dropped"], agg["new_carry_end_nofit_dropped"]
    print(f"\ncarry_end anchors: {tot_ce} total; kept {tot_ce - dup_ce - nofit_ce} ({100*(tot_ce-dup_ce-nofit_ce)/tot_ce:.2f}%); "
          f"true duplicate of the very next real point, correctly merged (no information lost): {dup_ce} ({100*dup_ce/tot_ce:.2f}%); "
          f"genuinely lost, no room to fit: {nofit_ce} ({100*nofit_ce/tot_ce:.2f}%)")
    print(f"\nOLD pre-fix logic (0.5m threshold, single pass, any kind), same raw anchors:")
    print(f"  dropped as 'duplicate' (<=0.5m): {agg['old_dup_dropped']} ({100*agg['old_dup_dropped']/total_anchors:.2f}%)")
    print(f"  dropped, no room to fit: {agg['old_nofit_dropped']}")
    print(f"  of which carry_end anchors silently lost: {agg['old_carry_end_lost']} "
          f"({100*agg['old_carry_end_lost']/max(agg['new_carry_end_raw'],1):.2f}% of all carry_end anchors)")
