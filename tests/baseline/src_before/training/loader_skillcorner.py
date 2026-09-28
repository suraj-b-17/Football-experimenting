"""Loads SkillCorner's open broadcast-tracking data (github.com/SkillCorner/opendata,
MIT — see LICENSES.md) into the same normalized shape as the other training
loaders: meters on a 105x68 pitch, x=0 at each team's OWN goal line.

Broadcast tracking is only a genuine measurement for players the camera can
actually see. SkillCorner's `*_tracking_extrapolated.jsonl` also fills in
off-screen players with its own model's estimate, flagged `is_detected: false`.
Those extrapolated positions are another model's guesses, not observations,
so they are NEVER used as training targets here — every player/ball sample
carries a `detected` flag and fit_mean_model.build_training_rows only emits
rows where both the player and (nearly) the ball were really seen.
"""
import json
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import cache

PITCH_X_M = 105.0
PITCH_Y_M = 68.0
REPO_RAW = "https://raw.githubusercontent.com/SkillCorner/opendata/master/data"
REPO_LFS = "https://media.githubusercontent.com/media/SkillCorner/opendata/master/data"

# Every match folder in the open release (verified against the repo's
# data/matches directory listing — all 20 contain tracking, despite the
# README's older "10 matches" wording).
MATCH_IDS = [
    "1874553", "1886347", "1899585", "1925299", "1927964", "1953632", "1959846",
    "1986691", "1996435", "1996436", "2006229", "2006363", "2007448", "2007721",
    "2010085", "2011166", "2013725", "2015213", "2016236", "2017461",
]

def _fetch(url, dest):
    if not os.path.exists(dest) or os.path.getsize(dest) < 1000:
        urllib.request.urlretrieve(url, dest)
    return dest

def _ts_seconds(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def load_match(match_id):
    meta_path = _fetch(f"{REPO_RAW}/matches/{match_id}/{match_id}_match.json",
                       cache("skillcorner", match_id, f"{match_id}_match.json"))
    trk_path = _fetch(f"{REPO_LFS}/matches/{match_id}/{match_id}_tracking_extrapolated.jsonl",
                      cache("skillcorner", match_id, f"{match_id}_tracking_extrapolated.jsonl"))
    meta = json.load(open(meta_path, encoding="utf-8"))
    L = float(meta.get("pitch_length") or PITCH_X_M)
    W = float(meta.get("pitch_width") or PITCH_Y_M)
    home_id = meta["home_team"]["id"]
    team_of = {p["id"]: ("Home" if p["team_id"] == home_id else "Away") for p in meta["players"]}

    p_rows, b_rows, poss_rows = [], [], []
    last_group = None
    with open(trk_path, encoding="utf-8") as f:
        for line in f:
            fr = json.loads(line)
            if fr["period"] is None or fr["timestamp"] is None:
                continue
            period, t = fr["period"], _ts_seconds(fr["timestamp"])
            b = fr["ball_data"]
            if b["x"] is not None:
                b_rows.append((period, t, (b["x"] / L + 0.5) * PITCH_X_M,
                               (b["y"] / W + 0.5) * PITCH_Y_M, bool(b["is_detected"])))
            g = fr["possession"]["group"]
            if g in ("home team", "away team") and g != last_group:
                poss_rows.append((period, t, "Home" if g == "home team" else "Away"))
                last_group = g
            for p in fr["player_data"]:
                team = team_of.get(p["player_id"])
                if team is None or p["x"] is None:
                    continue
                p_rows.append((period, t, f"{team}_{p['player_id']}", team,
                               (p["x"] / L + 0.5) * PITCH_X_M, (p["y"] / W + 0.5) * PITCH_Y_M,
                               bool(p["is_detected"])))

    players = pd.DataFrame(p_rows, columns=["period", "t", "player_id", "team", "x_m", "y_m", "detected"])
    ball = pd.DataFrame(b_rows, columns=["period", "t", "x_m", "y_m", "detected"])
    events = pd.DataFrame(poss_rows, columns=["Period", "Start Time [s]", "Team"])

    # Own-goal-at-x=0 per team per period: the player whose DETECTED positions
    # sit furthest from the halfway line on average is the goalkeeper — same
    # heuristic as the other loaders, restricted to real observations.
    flips = {}
    det = players[players["detected"]]
    for period in det["period"].unique():
        for team in ("Home", "Away"):
            window = det[(det["team"] == team) & (det["period"] == period)]
            means = window.groupby("player_id")["x_m"].agg(["mean", "size"])
            means = means[means["size"] >= 50]
            if means.empty:
                continue
            gk_mean = means.loc[(means["mean"] - PITCH_X_M / 2).abs().idxmax(), "mean"]
            flips[(team, period)] = gk_mean > PITCH_X_M / 2

    need_flip = np.array([flips.get((tm, pr), False) for tm, pr in zip(players["team"], players["period"])])
    players.loc[need_flip, "x_m"] = PITCH_X_M - players.loc[need_flip, "x_m"]
    players.loc[need_flip, "y_m"] = PITCH_Y_M - players.loc[need_flip, "y_m"]
    return {"players": players, "ball": ball, "events": events, "flips": flips}

if __name__ == "__main__":
    g = load_match(sys.argv[1] if len(sys.argv) > 1 else "1925299")
    p = g["players"]
    print(p.shape, g["ball"].shape, g["events"].shape, g["flips"])
    print("detected share of player samples: %.1f%%" % (100 * p["detected"].mean()))
    print("detected share of ball samples: %.1f%%" % (100 * g["ball"]["detected"].mean()))
    print(p[p["detected"]].groupby("player_id")["x_m"].mean().sort_values().head(3))
