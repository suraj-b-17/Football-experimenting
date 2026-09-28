"""Kickoff formation templates learned from real tracking (T1d).

Kickoff frames are found in the fully tracked matches (Metrica, DFL/IDSSE) as the
first 5 Hz frame of each cluster where the ball is within 1.5 m of the centre
spot and every observed player is in his own half (Law 8). For each player at
each kickoff: target = his real kickoff position; input = the median of his
SIMULATED sparse anchors in the following 300 s (the same kind of information
StatsBomb gives), plus role and whether his team kicks off. One linear fit per
(role, kicking) and axis; the residual SD is the soft-anchor uncertainty used in
production. Writes models/kickoff_template.json.
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from paths import MODEL_DIR
import build_rows_v2
import tracking_prep
import loader_metrica, loader_idsse

WINDOW_S = 300.0
CENTRE = np.array([52.5, 34.0])


def kickoff_frames(P):
    ball, bk = P["ball_home"], P["ball_known"]
    near = bk & (np.hypot(ball[:, 0] - CENTRE[0], ball[:, 1] - CENTRE[1]) < 1.5)
    x = P["own"][:, :, 0]
    own_half = np.all(np.where(P["known"], x <= 53.5, True), axis=0)
    n_known = P["known"].sum(0)
    cand = np.nonzero(near & own_half & (n_known >= 18))[0]
    out, last = [], -1e9
    for k in cand:
        if P["T"][k] - last > 30:
            out.append(k)
        last = P["T"][k]
    return out


def collect():
    rows = []
    for key in [("metrica", g) for g in loader_metrica.GAMES] + [("idsse", m) for m in loader_idsse.MATCH_IDS]:
        prep = tracking_prep.get(*key)
        sims = build_rows_v2.get(*key)["sims"]
        for period, P in prep.items():
            if period not in sims:
                continue
            anchors = sims[period]["anchors"]
            for k in kickoff_frames(P):
                t = P["T"][k]
                d = np.hypot(P["home"][:, k, 0] - P["ball_home"][k, 0], P["home"][:, k, 1] - P["ball_home"][k, 1])
                d[~P["known"][:, k]] = np.inf
                kick_team = P["team"][int(np.argmin(d))]
                for i, pid in enumerate(P["pids"]):
                    role = P["role"].get(pid)
                    if role is None or not P["known"][i, k]:
                        continue
                    A = anchors.get(pid, np.zeros((0, 3)))
                    w = A[(A[:, 0] >= t) & (A[:, 0] <= t + WINDOW_S)] if len(A) else A
                    rows.append({"key": f"{key[0]}/{key[1]}", "period": period, "t": t, "role": role,
                                 "kicking": int(P["team"][i] == kick_team),
                                 "kx": float(P["own"][i, k, 0]), "ky": float(P["own"][i, k, 1]),
                                 "mx": float(np.median(w[:, 1])) if len(w) else np.nan,
                                 "my": float(np.median(w[:, 2])) if len(w) else np.nan})
    return rows


def fit(rows):
    import pandas as pd
    df = pd.DataFrame(rows)
    tmpl = {"n_kickoffs": int(df.groupby(["key", "period", "t"]).ngroups), "n_player_kickoffs": int(len(df)),
            "window_s": WINDOW_S, "groups": {}}
    for (role, kick), g in df.groupby(["role", "kicking"]):
        ent = {"n": int(len(g)), "fallback_x": float(g["kx"].mean()), "fallback_y": float(g["ky"].mean()),
               "fallback_sd_x": float(g["kx"].std()), "fallback_sd_y": float(g["ky"].std())}
        h = g.dropna(subset=["mx"])
        if len(h) >= 8:
            bx = np.polyfit(h["mx"], h["kx"], 1); by = np.polyfit(h["my"], h["ky"], 1)
            rx = h["kx"] - np.polyval(bx, h["mx"]); ry = h["ky"] - np.polyval(by, h["my"])
            ent.update({"coef_x": bx.tolist(), "coef_y": by.tolist(), "sd_x": float(rx.std()), "sd_y": float(ry.std()),
                        "n_fit": int(len(h))})
        tmpl["groups"][f"{role}|{kick}"] = ent
    return tmpl, df


if __name__ == "__main__":
    tmpl, df = fit(collect())
    json.dump(tmpl, open(os.path.join(MODEL_DIR, "kickoff_template.json"), "w"), indent=1)
    print(json.dumps(tmpl, indent=1))
    print("player kickoff positions with own-frame x > 52.5 m:", int((df["kx"] > 52.5).sum()), "of", len(df))
