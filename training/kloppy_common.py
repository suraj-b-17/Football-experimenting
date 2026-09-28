"""Shared conversion from kloppy tracking/event datasets (in TRACAB centimeter
coordinates, pitch centered at 0) to this project's normalized training shape:
meters on a 105x68 pitch, x=0 at each team's OWN goal line."""
import numpy as np
import pandas as pd

PITCH_X_M = 105.0
PITCH_Y_M = 68.0

def _to_m(x, y):
    return x / 100.0 + PITCH_X_M / 2, y / 100.0 + PITCH_Y_M / 2

def from_kloppy(tracking, events):
    rows, ball_rows = [], []
    for r in tracking.records:
        period, t = r.period.id, r.timestamp.total_seconds()
        bc = r.ball_coordinates
        if bc is not None and not (np.isnan(bc.x) or np.isnan(bc.y)):
            ball_rows.append((period, t, *_to_m(bc.x, bc.y)))
        for player, coord in r.players_coordinates.items():
            if coord is None or np.isnan(coord.x):
                continue
            team = str(player.team.ground).capitalize()
            rows.append((period, t, f"{team}_{player.player_id}", team, *_to_m(coord.x, coord.y)))
    players = pd.DataFrame(rows, columns=["period", "t", "player_id", "team", "x_m", "y_m"])
    ball = pd.DataFrame(ball_rows, columns=["period", "t", "x_m", "y_m"])

    # own-goal-at-x=0: the player furthest from halfway on average is the GK
    flips = {}
    for period in players["period"].unique():
        for team in players["team"].unique():
            window = players[(players["team"] == team) & (players["period"] == period)]
            means = window.groupby("player_id")["x_m"].mean()
            if means.empty:
                continue
            gk_mean = means.loc[(means - PITCH_X_M / 2).abs().idxmax()]
            flips[(team, period)] = gk_mean > PITCH_X_M / 2

    need = np.array([flips.get((tm, pr), False) for tm, pr in zip(players["team"], players["period"])])
    players.loc[need, "x_m"] = PITCH_X_M - players.loc[need, "x_m"]
    players.loc[need, "y_m"] = PITCH_Y_M - players.loc[need, "y_m"]

    ev_rows = []
    for e in events.events:
        if e.coordinates is None or e.team is None or np.isnan(e.coordinates.x):
            continue
        team = str(e.team.ground).capitalize()
        x, y = _to_m(e.coordinates.x, e.coordinates.y)
        if flips.get((team, e.period.id), False):
            x, y = PITCH_X_M - x, PITCH_Y_M - y
        pid = f"{team}_{e.player.player_id}" if e.player is not None else None
        ev_rows.append((e.period.id, e.timestamp.total_seconds(), team, pid, str(e.event_type), x, y))
    ev = pd.DataFrame(ev_rows, columns=["Period", "Start Time [s]", "Team", "player_id", "Type", "Start X_m", "Start Y_m"])
    return {"players": players, "ball": ball, "events": ev, "flips": flips}
