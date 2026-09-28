"""PROTOTYPE, test-only — does not touch pipeline/build_match.py, not wired
into any rebuild.

For every flagged collision (the 650 drops, and the retimes with dist_m > 5m
from collision_distance_audit.csv): score BOTH conflicting anchors — the one
currently kept (the previous point in stream order) and the one currently
dropped/retimed — against independent context: the match's own ball path
(common.ballpath, built from every event including other players', so it is
not affected by this one player's own anchor conflict) and the nearest
same-time locations of OTHER players on the same team (a crude "where was the
run of play" signal). Fit = distance from the anchor's own location to the
ball-path position at the anchor's own time. Lower is better.

Reports: how often the currently-dropped/retimed point actually fits better
than the currently-kept one (i.e. the existing "always keep the earlier
point" rule picked the worse of the two), and what the new drop/retime counts
would be if the better-fitting point were kept instead.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline")); sys.path.insert(0, ROOT)
from common import ballpath
import build_match as BM

HERE = os.path.dirname(os.path.abspath(__file__))


def raw_points_for_player(match, pid):
    """Same construction as build_match.build_tracks, pre-collision-resolution,
    for one player only."""
    pts = []

    def add(t, x, y, kind, order):
        pts.append((t, x, y, kind, order))

    own_event_ts = []
    for e in match["events"]:
        if e.get("playerId") != pid or e["type"] in BM.NON_POSITIONAL or e["x"] is None:
            continue
        kind = "event_offcam" if e.get("offCamera") else "event"
        add(e["t"], e["x"], e["y"], kind, e["index"])
        own_event_ts.append(e["t"])
        if e.get("carryEnd"):
            add(e["t"] + e["duration"], e["carryEnd"][0], e["carryEnd"][1], "carry_end", e["index"] + 0.5)
            own_event_ts.append(e["t"] + e["duration"])
    own_event_ts.sort()
    import bisect
    for e in match["events"]:
        rid = e.get("passRecipientId")
        if e["type"] != "Pass" or rid != pid or e.get("passOutcome") != "Complete" or not e.get("passEnd"):
            continue
        t_arr = e["t"] + e["duration"]
        i = bisect.bisect_left(own_event_ts, t_arr - 0.5)
        if not (i < len(own_event_ts) and own_event_ts[i] <= t_arr + 0.5):
            add(t_arr, e["passEnd"][0], e["passEnd"][1], "reception", e["index"] + 0.6)
    for e in match["events"]:
        if e["type"] != "Shot":
            continue
        for ff in e.get("freezeFrame") or []:
            if ff.get("playerId") != pid:
                continue
            x, y = (ff["x"], ff["y"]) if ff["teammate"] else (105.0 - ff["x"], 68.0 - ff["y"])
            add(e["t"], x, y, "freeze", e["index"] + 0.7)
    return sorted(pts, key=lambda a: (round(a[0], 3), a[4]))


_cache = {}
_path_cache = {}


def get_match_context(mid):
    if mid not in _cache:
        from loader_statsbomb import load_match
        m = load_match(mid)
        home_id = m["home"]["id"]
        is_home = {}
        for tr in BM.build_tracks(m, use_freeze=False):
            is_home[tr["id"]] = tr["isHome"]
        _cache[mid] = (m, home_id, is_home)
        if len(_cache) > 40:
            _cache.pop(next(iter(_cache)))
    return _cache[mid]


def other_players_path(mid, m, exclude_pid):
    """Ball path built from every event EXCEPT this player's own — otherwise a
    player's own event is trivially its own ball-path knot (fit distance ~0
    for both candidates always), which is circular and uninformative. This is
    what makes the test 'does this location fit what OTHER players' events
    say the ball was doing', matching the brief."""
    key = (mid, exclude_pid)
    if key not in _path_cache:
        evs = [e for e in m["events"] if e.get("playerId") != exclude_pid]
        bev = BM.ball_events({**m, "events": evs})
        _path_cache[key] = ballpath.build_path(bev) if bev else None
        if len(_path_cache) > 400:
            _path_cache.pop(next(iter(_path_cache)))
    return _path_cache[key]


def fit_score(path, is_home, t, x, y):
    """Distance from (x,y), in the player's own team frame, to the (other-
    players-only) ball path's position at t, converted into that same frame."""
    if path is None:
        return np.nan
    bx, by = ballpath.sample(path, t)
    if not is_home:
        bx, by = 105.0 - bx, 68.0 - by
    return float(np.hypot(x - bx, y - by))


def process_case(mid, pid, t, kind):
    """Find the flagged point (t,kind) and its immediate predecessor in the
    player's raw (pre-resolution) anchor list; score both against a ball path
    built from every OTHER player's events."""
    m, home_id, is_home_map = get_match_context(mid)
    is_home = is_home_map.get(pid)
    if is_home is None:
        return None
    path = other_players_path(mid, m, pid)
    pts = raw_points_for_player(m, pid)
    idx = None
    for i, p in enumerate(pts):
        if abs(p[0] - t) < 0.02 and p[3] == kind:
            idx = i
            break
    if idx is None or idx == 0:
        return None
    prev = pts[idx - 1]
    cur = pts[idx]
    fit_prev = fit_score(path, is_home, prev[0], prev[1], prev[2])
    fit_cur = fit_score(path, is_home, cur[0], cur[1], cur[2])
    if np.isnan(fit_prev) or np.isnan(fit_cur):
        return None
    return {"match": mid, "playerId": pid, "kept_t": prev[0], "kept_kind": prev[3], "fit_kept": fit_prev,
            "flagged_t": cur[0], "flagged_kind": cur[3], "fit_flagged": fit_cur,
            "flagged_fits_better": fit_cur < fit_prev}


if __name__ == "__main__":
    df = pd.read_csv(os.path.join(HERE, "collision_distance_audit.csv"))
    dropped = df[df["type"] == "collision_dropped"]
    retimed_far = df[(df["type"] == "collision_retimed") & (df["dist_m"] > 5)]
    print(f"testing {len(dropped)} drops and {len(retimed_far)} retimes >5m")

    results = {"dropped": [], "retimed_far": []}
    for label, sub in (("dropped", dropped), ("retimed_far", retimed_far)):
        for _, row in sub.iterrows():
            r = process_case(row["match"], row["playerId"], row["t"], row["kind"])
            if r:
                results[label].append(r)
        n = len(results[label])
        better = sum(1 for r in results[label] if r["flagged_fits_better"])
        print(f"\n{label}: {n} cases scored (of {len(sub)} candidates)")
        print(f"  flagged (currently dropped/retimed) point fits the ball path BETTER than the kept point: "
              f"{better} ({100*better/max(n,1):.1f}%)")
        fk = np.array([r["fit_kept"] for r in results[label]])
        ff = np.array([r["fit_flagged"] for r in results[label]])
        print(f"  median fit distance: kept={np.median(fk):.2f}m flagged={np.median(ff):.2f}m")

    pd.DataFrame(results["dropped"] + results["retimed_far"]).to_csv(
        os.path.join(HERE, "collision_fit_prototype_results.csv"), index=False)

    # new counts if we always kept whichever of the pair fits the ball path better
    n_flip_drop = sum(1 for r in results["dropped"] if r["flagged_fits_better"])
    n_flip_retime = sum(1 for r in results["retimed_far"] if r["flagged_fits_better"])
    print(f"\nIf the better-fitting point were always kept instead of always the earlier one:")
    print(f"  of the {len(dropped)} drops: {n_flip_drop} would flip to keeping the (currently dropped) later point")
    print(f"  of the {len(retimed_far)} far retimes: {n_flip_retime} would flip to keeping the (currently kept) "
          f"earlier point's REPLACEMENT differently — i.e. the earlier point would be the one re-timed/dropped instead")
