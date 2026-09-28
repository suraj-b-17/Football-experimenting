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
            "possession": e.get("possession"),
            "playPattern": e.get("play_pattern", {}).get("name"),
            "possessionTeamId": e.get("possession_team", {}).get("id"),
            "teamId": team_id,
            "isHome": team_id == home_id,
            "playerId": e.get("player", {}).get("id"),
            "playerName": e.get("player", {}).get("name"),
            "x": _to_m(loc)[0] if loc else None,
            "y": _to_m(loc)[1] if loc else None,
            "duration": float(e.get("duration") or 0.0),
            "positionName": (e.get("position") or {}).get("name"),
            "underPressure": bool(e.get("under_pressure", False)),
            "counterpress": bool(e.get("counterpress", False)),
            "offCamera": bool(e.get("off_camera", False)),
            "relatedEvents": e.get("related_events", []),
        }
        p = e.get("pass")
        if p:
            end = p.get("end_location")
            ev["passEnd"] = _to_m(end) if end else None
            ev["passOutcome"] = p.get("outcome", {}).get("name", "Complete")
            ev["passRecipientId"] = p.get("recipient", {}).get("id")
            ev["passFlags"] = [k for k in ("through_ball", "cross", "switch", "cut_back") if p.get(k)]
        c = e.get("carry")
        if c and c.get("end_location"):
            ev["carryEnd"] = _to_m(c["end_location"])
        s = e.get("shot")
        if s:
            ev["shotOutcome"] = s.get("outcome", {}).get("name")
            ev["shotEnd"] = _to_m(s["end_location"][:2]) if s.get("end_location") else None
            ev["xg"] = s.get("statsbomb_xg")
            # Shooter's own frame. Opponents are converted into their own frame
            # where they are used (build_match.add_freeze_frame_anchors).
            ev["freezeFrame"] = [
                {"x": _to_m(ff["location"])[0], "y": _to_m(ff["location"])[1],
                 "teammate": ff["teammate"], "playerId": (ff.get("player") or {}).get("id"),
                 "positionName": (ff.get("position") or {}).get("name")}
                for ff in s.get("freeze_frame", [])
            ]
        # Cards live on foul_committed and bad_behaviour today; scan every
        # sub-object so a card on any other event type is not silently missed.
        for sub in e.values():
            if isinstance(sub, dict) and isinstance(sub.get("card"), dict):
                ev["card"] = sub["card"].get("name")
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

    # StatsBomb data quirk, found via QA and confirmed across ~130 of the 380
    # matches (systematic, not rare): the last "Ball Receipt*" of a period
    # (immediately before Half End) sometimes has its own `timestamp` reset to
    # near 00:00:00 while `period`/`index` stay correct — every neighbour by
    # stream index sits around 45-49 minutes. Sorting this player's anchors by
    # (corrupted) declared time then places it next to unrelated early-match
    # events instead of its true neighbours, producing a large, spurious
    # implied speed.
    #
    # The event's true time is unknown, but it is bracketed: it happened after
    # the previous event by stream index and before the next one whose own
    # time is not itself anomalous (in every case found, that neighbour is
    # Half End). Two candidate times are tried, in order: (1) prev_t + the
    # preceding event's own recorded `duration` (exact when the previous event
    # is the Pass this is the Ball Receipt for — a real StatsBomb field, nothing
    # invented), (2) the latest moment in the bracket (just before the next
    # reliable event), which is the most charitable placement — minimum implied
    # speed from the previous event's own location. Whichever candidate keeps
    # implied speed at or under the same MAX_SPEED_MPS used everywhere else in
    # this pipeline is kept, logged as retimed; if neither fits even the most
    # charitable placement, the event's true time truly cannot be recovered and
    # its location is dropped (never guessed) rather than retimed — it still
    # appears in the event feed/stats, just not as a position anchor.
    # CHANGELOG_fix.md D14.
    MAX_SPEED_MPS, MERGE_S = 9.5, 0.05  # match build_match.py's constants
    timestamp_anomalies = []
    by_period_idx = {}
    for ev in events:
        by_period_idx.setdefault(ev["period"], []).append(ev)
    for evs in by_period_idx.values():
        evs.sort(key=lambda e: e["index"])
        i = 1
        while i < len(evs):
            if evs[i]["t"] >= evs[i - 1]["t"] - 2.0:
                i += 1
                continue
            prev = evs[i - 1]
            j = i
            while j < len(evs) and evs[j]["t"] < prev["t"] - 2.0:
                j += 1
            bracket_hi = evs[j]["t"] if j < len(evs) else prev["t"] + 2.0
            for k in range(i, j):
                bad = evs[k]
                if bad["x"] is None:
                    continue  # nothing to anchor anyway
                candidates = []
                if prev.get("x") is not None and (prev.get("duration") or 0) > 0:
                    cand = prev["t"] + prev["duration"]
                    if prev["t"] < cand <= bracket_hi:
                        candidates.append(("prev_duration", cand))
                candidates.append(("bracket_end", max(bracket_hi - MERGE_S, prev["t"] + 0.001)))
                chosen = None
                for method, cand in candidates:
                    if prev.get("x") is None:
                        chosen = (method, cand)
                        break
                    d = ((bad["x"] - prev["x"]) ** 2 + (bad["y"] - prev["y"]) ** 2) ** 0.5
                    implied = d / max(cand - prev["t"], 1e-6)
                    if implied <= MAX_SPEED_MPS:
                        chosen = (method, cand)
                        break
                if chosen:
                    method, cand = chosen
                    timestamp_anomalies.append({"type": "timestamp_reset_retimed", "eventId": bad["id"],
                                                "playerId": bad.get("playerId"), "eventType": bad["type"],
                                                "declared_t": round(bad["t"], 3), "retimed_t": round(cand, 3),
                                                "bracket": [round(prev["t"], 3), round(bracket_hi, 3)], "method": method})
                    bad["t"] = cand
                else:
                    timestamp_anomalies.append({"type": "timestamp_reset_dropped", "eventId": bad["id"],
                                                "playerId": bad.get("playerId"), "eventType": bad["type"],
                                                "declared_t": round(bad["t"], 3),
                                                "bracket": [round(prev["t"], 3), round(bracket_hi, 3)]})
                    bad["x"] = bad["y"] = None
            i = j
    events.sort(key=lambda e: e["t"])

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
        "periodBounds": {p: (period_offset[p], period_offset[p] + period_end[p]) for p in sorted(period_end)},
        "_timestamp_anomalies": timestamp_anomalies,
    }

if __name__ == "__main__":
    d = load_match(sys.argv[1] if len(sys.argv) > 1 else "3754348")
    print(d["home"]["name"], d["home"]["score"], "-", d["away"]["score"], d["away"]["name"])
    print(len(d["events"]), "events, maxT=", round(d["maxT"]))
    print([e["type"] for e in d["events"][:8]])
