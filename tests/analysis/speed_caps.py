"""Real speed distributions from the tracking matches the shipped model is
trained on (Metrica x3 + DFL/IDSSE x7), to set the viewer's on-screen speed
caps from data rather than by hand.

Speeds are measured over a 1.0 s window (5 samples at 5 Hz): at 5 Hz a single
0.2 s step is dominated by tracking jitter, while 1 s is also the grain at which
a viewer perceives "dashing". Only samples with a real observation at both ends
count.

  run    every outfield player, every known sample
  carry  the player within CARRY_R of the ball (and nearest to it) at both
         ends of the window, while the ball itself moves < 12 m/s
         (i.e. travels with him, not passed past him)
  ball   ball speed over the same 1 s window (flight included)

Writes tests/analysis/speed_caps.json.
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "training"))
sys.path.insert(0, ROOT)
import tracking_prep  # noqa: E402

MATCHES = [("metrica", "Sample_Game_1"), ("metrica", "Sample_Game_2"), ("metrica", "Sample_Game_3")] + \
          [("idsse", m) for m in ("J03WMX", "J03WN1", "J03WOH", "J03WOY", "J03WPY", "J03WQQ", "J03WR9")]
W = 5          # samples = 1.0 s at 5 Hz
CARRY_R = 1.5  # m
Q = [50, 90, 99, 99.5, 99.9, 100]


def pct(a):
    a = np.asarray(a)
    return {f"p{q}": round(float(np.percentile(a, q)), 2) for q in Q} | {"n": int(a.size)}


run, carry, ball, run_gk = [], [], [], []
for src, mid in MATCHES:
    prep = tracking_prep.get(src, mid)
    for per, P in prep.items():
        H, K, B, BK = P["home"], P["known"], P["ball_home"], P["ball_known"]
        dt = W / tracking_prep.HZ
        pv = np.linalg.norm(H[:, W:] - H[:, :-W], axis=-1) / dt           # (p, n-W)
        pk = K[:, W:] & K[:, :-W]
        bv = np.linalg.norm(B[W:] - B[:-W], axis=-1) / dt
        bk = BK[W:] & BK[:-W]
        roles = [P["role"].get(pid) for pid in P["pids"]]
        for i, r in enumerate(roles):
            (run_gk if r == "GK" else run).append(pv[i][pk[i]])
        ball.append(bv[bk])
        d = np.linalg.norm(H - B[None], axis=-1)                          # (p, n)
        d = np.where(K & BK[None], d, np.inf)
        near = np.argmin(d, axis=0)
        dmin = d[near, np.arange(d.shape[1])]
        for j in range(d.shape[1] - W):
            i = near[j]
            if dmin[j] <= CARRY_R and near[j + W] == i and dmin[j + W] <= CARRY_R and bk[j] and bv[j] < 12 and pk[i, j]:
                carry.append(pv[i, j])
    print(src, mid, "done", flush=True)

run, run_gk, ball = np.concatenate(run), np.concatenate(run_gk), np.concatenate(ball)
res = {"window_s": W / tracking_prep.HZ, "matches": [f"{s}:{m}" for s, m in MATCHES],
       "run_outfield": pct(run), "run_gk": pct(run_gk), "carry": pct(carry), "ball": pct(ball)}
json.dump(res, open(os.path.join(HERE, "speed_caps.json"), "w"), indent=1)
print(json.dumps(res, indent=1))
