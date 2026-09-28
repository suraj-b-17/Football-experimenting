"""Loads Metrica Sports' open sample tracking + event data
(github.com/metrica-sports/sample-data — see LICENSES.md) into a normalized
form: pitch in meters (105x68), and for every player, x=0 is always THEIR OWN
goal line and x=105 the opponent's, regardless of which physical side they
were defending in a given period — the same "own attacking direction" frame
the application's event data is converted into, which is what makes the
model transferable.

Games 1 and 2 ship as CSV; Game 3 ships in FIFA EPTS format and is loaded via
kloppy into the identical shape.
"""
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import cache

PITCH_X_M = 105.0
PITCH_Y_M = 68.0
GAMES = ["Sample_Game_1", "Sample_Game_2", "Sample_Game_3"]

def data_dir():
    """Shallow-clones the official repo into the cache on first use."""
    root = os.path.dirname(cache("metrica", "x"))
    if not os.path.isdir(os.path.join(root, "data")):
        subprocess.run(["git", "clone", "-q", "--depth", "1",
                        "https://github.com/metrica-sports/sample-data.git", root], check=True)
    return os.path.join(root, "data")

def load_game3():
    from kloppy import metrica
    from kloppy_common import from_kloppy
    d = os.path.join(data_dir(), "Sample_Game_3")
    meta = os.path.join(d, "Sample_Game_3_metadata.xml")
    tracking = metrica.load_tracking_epts(meta_data=meta, raw_data=os.path.join(d, "Sample_Game_3_tracking.txt"),
                                          coordinates="tracab")
    events = metrica.load_event(event_data=os.path.join(d, "Sample_Game_3_events.json"), meta_data=meta,
                                coordinates="tracab")
    return from_kloppy(tracking, events)

def _load_tracking(path, team_label):
    raw = pd.read_csv(path, skiprows=2)
    cols = list(raw.columns)
    # Each entity (player or ball) is an (x, y) column PAIR by position — the y
    # header is blank ("Unnamed: N"), so pair positionally, not by name.
    meta_cols = ["Period", "Frame", "Time [s]"]
    entity_cols = [c for c in cols if c not in meta_cols]
    records = []
    ball_df = None
    for i in range(0, len(entity_cols), 2):
        xcol, ycol = entity_cols[i], entity_cols[i + 1]
        name = xcol  # e.g. "Player11" or "Ball"
        sub = raw[["Period", "Frame", "Time [s]", xcol, ycol]].copy()
        sub.columns = ["period", "frame", "t", "x", "y"]
        sub["x_m"] = sub["x"] * PITCH_X_M
        sub["y_m"] = sub["y"] * PITCH_Y_M
        if name == "Ball":
            ball_df = sub[["period", "frame", "t", "x_m", "y_m"]]
        else:
            sub["player_id"] = f"{team_label}_{name.replace('Player', '')}"
            sub["team"] = team_label
            records.append(sub[["period", "frame", "t", "player_id", "team", "x_m", "y_m"]])
    players = pd.concat(records, ignore_index=True)
    return players, ball_df

def _load_events(path):
    ev = pd.read_csv(path)
    ev.columns = [c.strip() for c in ev.columns]
    return ev

def load_game(game_name):
    if game_name == "Sample_Game_3":
        return load_game3()
    root = data_dir()
    home_path = os.path.join(root, game_name, f"{game_name}_RawTrackingData_Home_Team.csv")
    away_path = os.path.join(root, game_name, f"{game_name}_RawTrackingData_Away_Team.csv")
    ev_path = os.path.join(root, game_name, f"{game_name}_RawEventsData.csv")

    home_players, ball_h = _load_tracking(home_path, "Home")
    away_players, ball_a = _load_tracking(away_path, "Away")
    players = pd.concat([home_players, away_players], ignore_index=True)
    # ball tracking is duplicated across both team files; average where both present
    ball = ball_h.copy()
    ball["x_m"] = np.nanmean(np.vstack([ball_h["x_m"], ball_a["x_m"]]), axis=0)
    ball["y_m"] = np.nanmean(np.vstack([ball_h["y_m"], ball_a["y_m"]]), axis=0)

    events = _load_events(ev_path)

    # --- Determine each team's own-goal side per period, then normalize so
    # x=0 is always the team's own goal.
    # Kickoff position isn't diagnostic (both teams cluster near center in the
    # first seconds) -- use whichever player sits most consistently near one
    # end across the WHOLE period, i.e. the goalkeeper. ---
    flips = {}  # (team, period) -> True if needs x,y flip
    for period in players["period"].unique():
        for team in ["Home", "Away"]:
            window = players[(players["team"] == team) & (players["period"] == period)]
            per_player_mean = window.groupby("player_id")["x_m"].mean()
            gk_mean = per_player_mean.loc[(per_player_mean - PITCH_X_M / 2).abs().idxmax()]
            flips[(team, period)] = gk_mean > PITCH_X_M / 2

    def apply_flip(df, team_col="team"):
        df = df.copy()
        need_flip = df.apply(lambda r: flips.get((r[team_col], r["period"]), False), axis=1)
        df.loc[need_flip, "x_m"] = PITCH_X_M - df.loc[need_flip, "x_m"]
        df.loc[need_flip, "y_m"] = PITCH_Y_M - df.loc[need_flip, "y_m"]
        return df

    players = apply_flip(players)

    # Ball and events need a per-event team reference to know which flip to apply;
    # events already have a Team column. For ball frames we tag with whichever
    # team is "in possession" at that time (nearest preceding event), just for
    # the purpose of a *team-relative* ball position used in the regression.
    events["Start X_m"] = events["Start X"] * PITCH_X_M
    events["Start Y_m"] = events["Start Y"] * PITCH_Y_M
    events["End X_m"] = events["End X"] * PITCH_X_M
    events["End Y_m"] = events["End Y"] * PITCH_Y_M
    ev_flip = events.apply(lambda r: flips.get((r["Team"], r["Period"]), False), axis=1)
    events.loc[ev_flip, "Start X_m"] = PITCH_X_M - events.loc[ev_flip, "Start X_m"]
    events.loc[ev_flip, "Start Y_m"] = PITCH_Y_M - events.loc[ev_flip, "Start Y_m"]
    events.loc[ev_flip, "End X_m"] = PITCH_X_M - events.loc[ev_flip, "End X_m"]
    events.loc[ev_flip, "End Y_m"] = PITCH_Y_M - events.loc[ev_flip, "End Y_m"]

    return {"players": players, "ball": ball, "events": events, "flips": flips}

if __name__ == "__main__":
    g = load_game(sys.argv[1] if len(sys.argv) > 1 else "Sample_Game_1")
    print(g["players"].shape, g["ball"].shape, g["events"].shape)
    print(g["players"]["player_id"].unique())
    print(g["players"].groupby("player_id")["x_m"].mean().sort_values())
