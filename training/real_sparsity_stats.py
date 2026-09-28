"""Real StatsBomb anchor sparsity over the whole 2015/16 season, using the
pipeline's own build_tracks: per-role gaps between consecutive real anchors
(event / carry_end / reception; freeze-frame anchors excluded because the
simulator does not produce them), anchors per 90 minutes, the share of anchors
that are on-ball events, and how far off-ball anchors are from the ball proxy.
Writes training/real_sparsity.json and a raw gap sample for plotting/tests.
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
from loader_statsbomb import load_match
from build_match import build_tracks, ball_events, role_at, BALL_EVENT_TYPES, NON_POSITIONAL
from common import ballpath
from common.features import role_from_position_name


def gaps_for_match(mid):
    m = load_match(mid)
    tracks = build_tracks(m, use_freeze=False)
    path = ballpath.build_path(ball_events(m))
    type_at = {}
    for e in m["events"]:
        if e.get("playerId") is not None and e["x"] is not None and e["type"] not in NON_POSITIONAL:
            type_at[(e["playerId"], round(e["t"], 3))] = e["type"]
    out = {"gaps": {}, "per90": {}, "onball": 0, "offball": 0, "offball_dist": []}
    for tr in tracks:
        A = [a for a in tr["anchors"] if a[3] in ("event", "event_offcam", "carry_end", "reception")]
        mins = sum(b - a for a, b in tr["intervals"]) / 60.0
        if mins < 10 or not A:
            continue
        role = role_from_position_name(role_at(tr["roles"], A[0][0]))
        ts = np.array([a[0] for a in A])
        for a, b in tr["intervals"]:
            seg = ts[(ts >= a) & (ts <= b)]
            if len(seg) > 1:
                out["gaps"].setdefault(role, []).extend(np.diff(seg).tolist())
        out["per90"].setdefault(role, []).append(len(A) / mins * 90)
        for a in A:
            typ = type_at.get((tr["id"], round(a[0], 3)))
            if typ in BALL_EVENT_TYPES or a[3] in ("carry_end", "reception"):
                out["onball"] += 1
            else:
                out["offball"] += 1
                bx, by = ballpath.sample(path, a[0])
                x, y = ballpath.to_own_frame(bx, by, tr["isHome"])
                out["offball_dist"].append(float(np.hypot(x - a[1], y - a[2])))
    return out


def summarize(gaps, per90, onball, offball, offd):
    res = {}
    for role in ("GK", "DEF", "MID", "FWD"):
        g = np.array(gaps.get(role, []))
        g = g[g > 0.05]
        res[role] = {"n_gaps": int(len(g)), "median": float(np.median(g)), "p75": float(np.percentile(g, 75)),
                     "p90": float(np.percentile(g, 90)), "p99": float(np.percentile(g, 99)), "max": float(g.max()),
                     "anchors_per90_median": float(np.median(per90.get(role, [0])))}
    res["onball_share"] = onball / max(onball + offball, 1)
    offd = np.array(offd)
    res["offball_dist_to_ball"] = {"median": float(np.median(offd)), "p90": float(np.percentile(offd, 90))} if len(offd) else {}
    return res


if __name__ == "__main__":
    import glob
    sys.path.insert(0, os.path.join(ROOT, "pipeline"))
    from run_season import all_match_ids
    ids = [mid for mid, _ in all_match_ids(2, 27)]
    if len(sys.argv) > 1:
        ids = ids[: int(sys.argv[1])]
    from concurrent.futures import ProcessPoolExecutor
    gaps, per90, onb, offb, offd = {}, {}, 0, 0, []
    with ProcessPoolExecutor(max_workers=12) as ex:
        for r in ex.map(gaps_for_match, ids):
            for k, v in r["gaps"].items(): gaps.setdefault(k, []).extend(v)
            for k, v in r["per90"].items(): per90.setdefault(k, []).extend(v)
            onb += r["onball"]; offb += r["offball"]; offd += r["offball_dist"]
    res = summarize(gaps, per90, onb, offb, offd)
    res["n_matches"] = len(ids)
    json.dump(res, open(os.path.join(HERE, "real_sparsity.json"), "w"), indent=1)
    np.savez_compressed(os.path.join(HERE, "real_gaps_sample.npz"),
                        **{r: np.random.default_rng(0).choice(np.array(gaps[r]), size=min(200000, len(gaps[r])), replace=False) for r in gaps})
    print(json.dumps(res, indent=1))
