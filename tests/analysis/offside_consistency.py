"""Structural check against REAL StatsBomb information (no tracking needed).

At the moment a pass is played, the intended recipient is in an offside
position iff he is in the opponents' half, ahead of the ball, and beyond the
second-last opponent (Law 11). StatsBomb tells us which passes were flagged
offside ("Pass Offside" outcome). A reconstruction with realistic structure
should put the recipient offside for those, and onside for completed passes.

Usage: python offside_consistency.py <payload_dir> [n_matches]
Payload format: new (segs) or old (smoothed) — both supported.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

L, W = 105.0, 68.0


def pos_fn(tr):
    if tr.get("segs"):
        segs = tr["segs"]
        def f(t):
            for s in segs:
                if s["t"][0] - 1e-6 <= t <= s["t"][-1] + 1e-6:
                    return np.interp(t, s["t"], s["x"]), np.interp(t, s["t"], s["y"])
            return None
        return f
    s = tr.get("smoothed")
    if not s:
        return lambda t: None
    return lambda t: (np.interp(t, s["t"], s["x"]), np.interp(t, s["t"], s["y"]))


def active(tr, t):
    if "intervals" in tr:
        return any(a <= t <= b for a, b in tr["intervals"])
    return (tr.get("enter") is None or t >= tr["enter"]) and (tr.get("exit") is None or t <= tr["exit"])


def one(path):
    if path.endswith(".js"):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pipeline"))
        from payload_io import read_js
        d = read_js(path)
    else:
        d = json.load(open(path, encoding="utf-8"))
    teams = {d["home"]["id"]: ("home", "away"), d["away"]["id"]: ("away", "home")}
    fns = {tr["id"]: (pos_fn(tr), tr) for s in ("home", "away") for tr in d[s]["tracks"]}
    events = d["events"]
    # recipient: from the old payload's receptions or the event stream (next Ball Receipt is not reliable);
    # use StatsBomb's own recipient via receptions (new payload) — both payloads carry receptions for
    # completed passes only, so recipients for Pass Offside come from the raw loader below.
    return d, teams, fns, events


def evaluate(payload_dir, n):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pipeline"))
    from loader_statsbomb import load_match
    files = sorted(f for f in os.listdir(payload_dir) if f.endswith((".json", ".js")))[:n]
    rows = []
    for f in files:
        d, teams, fns, _ = one(os.path.join(payload_dir, f))
        mid = f.rsplit(".", 1)[0]
        m = load_match(mid)
        for e in m["events"]:
            rid = e.get("passRecipientId")
            if e["type"] != "Pass" or rid is None or e["x"] is None or e.get("passOutcome") not in ("Complete", "Pass Offside"):
                continue
            if e.get("playPattern") in ("From Throw In", "From Goal Kick", "From Corner"):
                continue  # no offside from these restarts
            own, opp = teams[e["teamId"]]
            if rid not in fns:
                continue
            fR, trR = fns[rid]
            if not active(trR, e["t"]):
                continue
            pr = fR(e["t"])
            if pr is None:
                continue
            ox = []
            for tr in d[opp]["tracks"]:
                if not active(tr, e["t"]):
                    continue
                p = fns[tr["id"]][0](e["t"])
                if p is not None:
                    ox.append(L - p[0])  # opponent in the passer's frame
            if len(ox) < 10:
                continue
            line = sorted(ox)[-2]   # second-last opponent (incl. keeper)
            offside = pr[0] > line and pr[0] > L / 2 and pr[0] > e["x"]
            rows.append({"match": mid, "outcome": e["passOutcome"], "recon_offside": bool(offside),
                         "recipient_minus_line_m": float(pr[0] - line)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = evaluate(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 40)
    g = df.groupby("outcome").agg(n=("recon_offside", "size"), share_reconstructed_offside=("recon_offside", "mean"),
                                  median_recipient_minus_line_m=("recipient_minus_line_m", "median"))
    print(g.round(3).to_string())
