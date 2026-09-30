"""Polish item 1 evidence: on-screen speed over playback time, before vs after
pacing, for the 5 chosen "super dash" examples (tests/drivers/dash_series.js).
Writes tests/evidence/polish/dash_before_after.png and prints a summary table."""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
EV = os.path.join(os.path.dirname(HERE), "evidence", "polish")
CAPS = {"carry": 7.5, "run": 8.0}

rows = []
for m in ("3754129", "3754348", "3754258"):
    b = json.load(open(os.path.join(EV, f"dash_series_before_{m}.json")))
    a = json.load(open(os.path.join(EV, f"dash_series_after_{m}.json")))
    rows += [(m, x, y) for x, y in zip(b, a)]
rows.sort(key=lambda r: r[1]["label"])

fig, axes = plt.subplots(len(rows), 1, figsize=(10, 2.6 * len(rows)), constrained_layout=True)
print("| example | match | peak before | peak after | dash in playback before | after | change |")
print("|---|---|---:|---:|---:|---:|---:|")
for ax, (m, b, a) in zip(axes, rows):
    cap = CAPS["carry"] if "Carry" in b["label"] else CAPS["run"]
    for d, col, name in ((b, "#9CA3AF", "before (1:1 match clock)"), (a, "#2563EB", "after (paced)")):
        pb = [s[0] for s in d["series"]]
        v = [None if (len(s) > 3 and s[3]) else s[2] for s in d["series"]]
        ax.plot(pb, v, color=col, lw=1.6, label=name)
        runs, cur = [], None
        for s in d["series"]:
            if len(s) > 3 and s[3]:
                cur = [s[0], s[0]] if cur is None else [cur[0], s[0]]
            elif cur is not None:
                runs.append(cur); cur = None
        if cur is not None:
            runs.append(cur)
        for i, (r0, r1) in enumerate(runs):
            ax.axvspan(r0, r1, color="#111827", alpha=0.18, hatch="//", label="stoppage skip (dimmed)" if i == 0 else None)
        span = [s[0] for s in d["series"] if d["t0"] <= s[1] <= d["t1"]]
        if span:
            ax.axvspan(min(span), max(span), color=col, alpha=0.12)
    ax.axhline(cap, color="#DC2626", ls="--", lw=1, label=f"cap {cap} m/s")
    ax.set_ylim(0, min(max(12, max(s[2] for s in b["series"]) * 1.05), 30))
    ax.set_title(f"{b['label']}  —  match {m}", fontsize=10, loc="left")
    ax.set_ylabel("on-screen m/s")
    ax.legend(fontsize=8, loc="upper right")
    d0, d1 = b["dash_playback_s"], a["dash_playback_s"]
    print(f"| {b['label']} | {m} | {b['peak_mps']:.1f} | {a['peak_mps']:.1f} | {d0:.2f} s | {d1:.2f} s | {d1 - d0:+.2f} s ({(d1 / d0 - 1) * 100:+.0f} %) |")
axes[-1].set_xlabel("playback seconds at 1x (from 2 s before the dash)")
out = os.path.join(EV, "dash_before_after.png")
fig.savefig(out, dpi=110)
print("wrote", out)
