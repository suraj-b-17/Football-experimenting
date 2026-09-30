"""Real acceleration (change of speed) distribution from the tracking matches
the shipped model is trained on (Metrica x3 + DFL/IDSSE x7) -- the target for
how quickly a drawn player may speed up or slow down.

Speed is measured over a 1.0 s window (as in speed_caps.py), acceleration as
the change of that speed over the next 1.0 s: |v(t+1) - v(t)| / 1 s. Shorter
windows at 5 Hz are dominated by tracking jitter. Outfield players only.
Writes tests/analysis/accel_caps.json.
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
HZ = tracking_prep.HZ
Q = [50, 90, 99, 99.5, 99.9]
out = {}
for win_s in (0.6, 1.0):
    W = int(round(win_s * HZ))
    acc_all, acc_fast = [], []
    for src, mid in MATCHES:
        for P in tracking_prep.get(src, mid).values():
            H, K = P["home"], P["known"]
            gk = np.array([P["role"].get(p) == "GK" for p in P["pids"]])
            v = np.linalg.norm(H[:, W:] - H[:, :-W], axis=-1) / win_s            # speed over [i, i+W]
            vk = K[:, W:] & K[:, :-W]
            a = np.abs(v[:, W:] - v[:, :-W]) / win_s                            # change over the next window
            ak = vk[:, W:] & vk[:, :-W]
            vmax = np.maximum(v[:, W:], v[:, :-W])
            for i in np.where(~gk)[0]:
                acc_all.append(a[i][ak[i]])
                acc_fast.append(a[i][ak[i] & (vmax[i] > 5.0)])
    acc_all, acc_fast = np.concatenate(acc_all), np.concatenate(acc_fast)
    out[f"window_{win_s}s"] = {
        "all": {f"p{q}": round(float(np.percentile(acc_all, q)), 2) for q in Q} | {"n": int(acc_all.size)},
        "when_faster_than_5mps": {f"p{q}": round(float(np.percentile(acc_fast, q)), 2) for q in Q} | {"n": int(acc_fast.size)},
    }
json.dump(out, open(os.path.join(HERE, "accel_caps.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
