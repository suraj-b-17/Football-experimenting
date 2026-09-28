"""Loads a single match's StatsBomb open-data event stream
(github.com/statsbomb/open-data — see LICENSES.md; fetched live, never
redistributed) and normalizes it into meters on a 105x68 pitch, x=0 at each
team's own goal — the SAME convention `training/`'s loaders use, so the
application data lines up with what the model was trained on.

StatsBomb's own coordinate system is already "each team's own goal at x=0,
attacking toward x=120" (not raw camera side) on a 120x80 unit pitch, unlike
some other event feeds which need a same-side detection heuristic. This
loader only rescales units to meters; it does not need to detect or flip
attacking sides.

No 360 (freeze-frame tracking) data exists for competition_id=2, season_id=27
(2015/16 Premier League) — confirmed directly against data/competitions.json
(`match_available_360` is null) and every match's `match_status_360` in
data/matches/2/27.json (always "scheduled" or "processing", never
"available"). This loader uses only standard event data plus StatsBomb's
built-in shot freeze frames (a real, if sparse, extra position source at
shot moments only).
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import cache

SB_X, SB_Y = 120.0, 80.0   # StatsBomb's own coordinate units
PITCH_X_M, PITCH_Y_M = 105.0, 68.0
REPO_RAW = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

# Real dead-ball stoppage evidence, read directly off StatsBomb's own
# play_pattern tag on the restart event — no heuristic guessing needed.
RESTART_LABELS = {
    "From Throw In": "Throw-in",
    "From Goal Kick": "Goal kick",
    "From Corner": "Corner",
    "From Free Kick": "Free kick",
    # StatsBomb has no distinct "Goal" event type (a goal is a Shot event
    # with shot.outcome.name == "Goal") — verified directly that every
    # mid-period "From Kick Off" restart is preceded by exactly that (the
    # only OTHER time "From Kick Off" appears is the real match kickoff,
    # which isn't a mid-match gap at all since Half Start/Half End aren't
    # in the event stream this scans), so the tag alone is a reliable signal
    # here without needing to separately detect the preceding goal event.
    "From Kick Off": "Goal — restart",
}

def _fetch(url, dest):
    if not os.path.exists(dest) or os.path.getsize(dest) < 100:
        urllib.request.urlretrieve(url, dest)
    return dest

def _to_m(loc):
    x, y = loc
    return x / SB_X * PITCH_X_M, y / SB_Y * PITCH_Y_M

def _hms(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def load_match(match_id, competition_id=2, season_id=27):
    match_id = str(match_id)
    ev_path = _fetch(f"{REPO_RAW}/events/{match_id}.json", cache("statsbomb", "events", f"{match_id}.json"))
    lu_path = _fetch(f"{REPO_RAW}/lineups/{match_id}.json", cache("statsbomb", "lineups", f"{match_id}.json"))
    matches_path = _fetch(f"{REPO_RAW}/matches/{competition_id}/{season_id}.json",
                          cache("statsbomb", "matches", f"{competition_id}_{season_id}.json"))

    with open(ev_path, encoding="utf-8") as f:
        raw = json.load(f)
    with open(lu_path, encoding="utf-8") as f:
        lineups = json.load(f)
    with open(matches_path, encoding="utf-8") as f:
        all_matches = json.load(f)
    minfo = next(m for m in all_matches if str(m["match_id"]) == match_id)

    home_id, away_id = minfo["home_team"]["home_team_id"], minfo["away_team"]["away_team_id"]

    events = []
    for e in raw:
        team_id = e.get("team", {}).get("id")
        loc = e.get("location")
        ev = {
            "id": e["id"], "index": e["index"], "period": e["period"],
            "t_in_period": _hms(e["timestamp"]),
            "minute": e["minute"], "second": e["second"],
            "type": e["type"]["name"],
            "playPattern": e.get("play_pattern", {}).get("name"),
            "possessionTeamId": e.get("possession_team", {}).get("id"),
            "teamId": team_id,
            "isHome": team_id == home_id,
            "playerId": e.get("player", {}).get("id"),
            "playerName": e.get("player", {}).get("name"),
            "x": _to_m(loc)[0] if loc else None,
            "y": _to_m(loc)[1] if loc else None,
        }
        p = e.get("pass")
        if p:
            end = p.get("end_location")
            ev["passEnd"] = _to_m(end) if end else None
            ev["passOutcome"] = p.get("outcome", {}).get("name", "Complete")
            ev["passRecipientId"] = p.get("recipient", {}).get("id")
            ev["passHeight"] = p.get("height", {}).get("name")
        c = e.get("carry")
        if c and c.get("end_location"):
            ev["carryEnd"] = _to_m(c["end_location"])
            ev["duration"] = e.get("duration", 0.0)
        s = e.get("shot")
        if s:
            ev["shotOutcome"] = s.get("outcome", {}).get("name")
            ev["shotEnd"] = _to_m(s["end_location"][:2]) if s.get("end_location") else None
            ev["xg"] = s.get("statsbomb_xg")
            ev["freezeFrame"] = [
                {"x": _to_m(ff["location"])[0], "y": _to_m(ff["location"])[1],
                 "teammate": ff["teammate"], "playerId": ff.get("player", {}).get("id")}
                for ff in s.get("freeze_frame", [])
            ]
        fc = e.get("foul_committed")
        if fc:
            ev["card"] = fc.get("card", {}).get("name")
        bb = e.get("bad_behaviour")
        if bb:
            ev["card"] = bb.get("card", {}).get("name")
        sub = e.get("substitution")
        if sub:
            ev["subReplacementId"] = sub.get("replacement", {}).get("id")
        tac = e.get("tactics")
        if tac:
            ev["formation"] = tac.get("formation")
            ev["tacticsLineup"] = [
                {"playerId": pl["player"]["id"], "positionId": pl["position"]["id"],
                 "positionName": pl["position"]["name"], "jersey": pl["jersey_number"]}
                for pl in tac.get("lineup", [])
            ]
        events.append(ev)

    # Continuous match-clock seconds across periods: period boundaries come
    # from each period's own "Half Start" events (period 1 starts at 0;
    # period 2 continues from period 1's actual final timestamp, etc.)
    period_end = {}
    for e in raw:
        period_end[e["period"]] = max(period_end.get(e["period"], 0.0), _hms(e["timestamp"]))
    period_offset = {1: 0.0}
    for p in sorted(period_end):
        if p > 1:
            period_offset[p] = period_offset[p - 1] + period_end[p - 1]
    for ev in events:
        ev["t"] = period_offset.get(ev["period"], 0.0) + ev["t_in_period"]
        del ev["t_in_period"]

    id2name = {}
    for team_lu in lineups:
        for p in team_lu["lineup"]:
            id2name[p["player_id"]] = p["player_nickname"] or p["player_name"]

    return {
        "matchId": match_id,
        "home": {"id": home_id, "name": minfo["home_team"]["home_team_name"], "score": minfo["home_score"]},
        "away": {"id": away_id, "name": minfo["away_team"]["away_team_name"], "score": minfo["away_score"]},
        "date": minfo["match_date"], "stadium": (minfo.get("stadium") or {}).get("name"),
        "referee": (minfo.get("referee") or {}).get("name"),
        "events": events,
        "id2name": id2name,
        "lineups": lineups,
        "maxT": max((e["t"] for e in events), default=0.0),
        "periodOffset": period_offset,
    }

if __name__ == "__main__":
    d = load_match(sys.argv[1] if len(sys.argv) > 1 else "3754348")
    print(d["home"]["name"], d["home"]["score"], "-", d["away"]["score"], d["away"]["name"])
    print(len(d["events"]), "events, maxT=", round(d["maxT"]))
    print([e["type"] for e in d["events"][:8]])
