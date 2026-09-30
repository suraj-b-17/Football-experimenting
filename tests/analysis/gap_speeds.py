"""Polish item 1 diagnosis: speed REQUIRED to cover each real gap in exactly its
real time window, if playback is forced to map match time 1:1.

  off-ball  consecutive real anchors of one player that are not a carry
            (straight-line distance / real time between them)
  carry     a StatsBomb Carry (start -> end_location over its real duration)
  ball      (a) flights: Pass / Shot start -> end over the real duration
            (b) handoffs: previous ball event's end -> next ball event's start,
                over the real time between them

Caps come from real tracking (tests/analysis/speed_caps.py): running 8.0 m/s
(p99.9 of 1 s windows), carrying 7.5 m/s (p99), ball 30 m/s (p99.9). For
off-ball gaps a duration-matched cap is also reported (real p99.9 over a window
as long as the gap: long gaps are held to a lower sustained speed).

Reads the built payloads (anchors exactly as displayed, after collision
handling) and the cached StatsBomb events (for the ball).
Usage: python tests/analysis/gap_speeds.py [n_matches | match ids...]
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

RUN_CAP, CARRY_CAP, BALL_CAP = 8.0, 7.5, 30.0
# real p99.9 of outfield speed over a window of this length (speed_caps.py, window sweep)
WIN = np.array([0.2, 0.4, 1.0, 2.0, 5.0, 10.0])
WIN_P999 = np.array([8.03, 7.99, 7.90, 7.72, 6.92, 5.51])


def dur_cap(dt):
    return np.interp(dt, WIN, WIN_P999)


def one(mid):
    from payload_io import read_js
    from loader_statsbomb import load_match
    from build_match import ball_events
    from common.ballpath import clean_events
    d = read_js(os.path.join(ROOT, "output", "data", f"{mid}.js"))
    off, carry = [], []
    for side in ("home", "away"):
        for tr in d[side]["tracks"]:
            A = [a for a in tr["anchors"] if a[3] != 4]  # off-camera anchors are soft
            for a, b in zip(A, A[1:]):
                dt = b[0] - a[0]
                if dt <= 0 or not any(ia <= a[0] <= ib and ia <= b[0] <= ib for ia, ib in tr["intervals"]):
                    continue
                if b[3] == 1:  # carry_end: this pair is the carry itself
                    continue
                off.append((np.hypot(b[1] - a[1], b[2] - a[2]), dt, a[3] == 3 or b[3] == 3))
            for c in tr["carries"]:
                t0, t1, x0, y0, x1, y1, _ = c
                carry.append((np.hypot(x1 - x0, y1 - y0), t1 - t0))
    m = load_match(mid)
    bev = clean_events(ball_events(m))
    flight, hand = [], []
    for e in bev:
        if e["t1"] > e["t0"] + 1e-6:
            flight.append((np.hypot(e["x1"] - e["x0"], e["y1"] - e["y0"]), e["t1"] - e["t0"]))
    for a, b in zip(bev, bev[1:]):
        dist = np.hypot(b["x0"] - a["x1"], b["y0"] - a["y1"])
        if dist > 0.5:
            hand.append((dist, b["t0"] - a["t1"]))
    return mid, off, carry, flight, hand


def share(dist, dt, cap):
    v = dist / np.maximum(dt, 1e-3)
    return v, float(np.mean(v > cap))


if __name__ == "__main__":
    ids = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "output", "data")) if f.endswith(".js"))
    if len(sys.argv) > 1:
        ids = ids[: int(sys.argv[1])] if sys.argv[1].isdigit() and len(sys.argv) == 2 and len(sys.argv[1]) < 5 else sys.argv[1:]
    with ProcessPoolExecutor(10) as ex:
        res = list(ex.map(one, ids))
    off = np.array([o for r in res for o in r[1]]).reshape(-1, 3)
    carry = np.array([o for r in res for o in r[2]]).reshape(-1, 2)
    flight = np.array([o for r in res for o in r[3]]).reshape(-1, 2)
    hand = np.array([o for r in res for o in r[4]]).reshape(-1, 2)
    out = {"matches": len(ids), "caps": {"run": RUN_CAP, "carry": CARRY_CAP, "ball": BALL_CAP}}

    v, s = share(off[:, 0], off[:, 1], RUN_CAP)
    vdc = off[:, 0] / np.maximum(off[:, 1], 1e-3) > dur_cap(off[:, 1])
    nf = off[:, 2] == 0
    out["off_ball"] = {"gaps": int(len(off)), "share_gt_run_cap": round(s, 4),
                       "share_gt_duration_matched_real_p99_9": round(float(vdc.mean()), 4),
                       "share_gt_9_5": round(float(np.mean(v > 9.5)), 4),
                       "excluding_freeze_frame_pairs": {"gaps": int(nf.sum()), "share_gt_run_cap": round(float(np.mean(v[nf] > RUN_CAP)), 4)},
                       "median_mps": round(float(np.median(v)), 2), "p99_mps": round(float(np.percentile(v, 99)), 2),
                       "by_gap_len": {}}
    for lo, hi in ((0, 1), (1, 3), (3, 10), (10, 60), (60, 1e9)):
        mk = (off[:, 1] >= lo) & (off[:, 1] < hi)
        out["off_ball"]["by_gap_len"][f"{lo}-{hi if hi < 1e9 else 'inf'}s"] = {
            "n": int(mk.sum()), "share_gt_run_cap": round(float(np.mean(v[mk] > RUN_CAP)), 4),
            "share_gt_duration_matched": round(float(np.mean(vdc[mk])), 4)}
    v, s = share(carry[:, 0], carry[:, 1], CARRY_CAP)
    out["carry"] = {"carries": int(len(carry)), "share_gt_carry_cap": round(s, 4), "share_gt_9_5": round(float(np.mean(v > 9.5)), 4),
                    "median_mps": round(float(np.median(v)), 2), "p99_mps": round(float(np.percentile(v, 99)), 2)}
    v, s = share(flight[:, 0], flight[:, 1], BALL_CAP)
    out["ball_flight"] = {"flights": int(len(flight)), "share_gt_ball_cap": round(s, 4),
                          "median_mps": round(float(np.median(v)), 2), "p99_mps": round(float(np.percentile(v, 99)), 2)}
    v, s = share(hand[:, 0], hand[:, 1], BALL_CAP)
    out["ball_handoff"] = {"handoffs_gt_0_5m": int(len(hand)), "share_gt_ball_cap": round(s, 4),
                           "share_zero_time": round(float(np.mean(hand[:, 1] <= 1e-3)), 4),
                           "median_dist_m": round(float(np.median(hand[:, 0])), 2)}
    json.dump(out, open(os.path.join(HERE, "gap_speeds.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))
