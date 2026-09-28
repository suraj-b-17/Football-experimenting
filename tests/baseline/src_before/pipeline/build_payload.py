"""Builds one match's viewer payload: output/data/<match_id>.json.
Generic — works for any match_id in the configured competition/season, not
one hardcoded match (see run_season.py for looping over all of them)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import OUTPUT_DIR
from loader_statsbomb import load_match, PITCH_X_M, PITCH_Y_M
from build_match import detect_stoppages
from apply_to_match import apply

def build(match_id, competition_id=2, season_id=27):
    match = load_match(match_id, competition_id, season_id)
    tracks, home_s, away_s = apply(match)
    stoppages = detect_stoppages(match)

    by_id = {t["id"]: t for t in tracks}

    def team_payload(team_id, smoothed):
        out = []
        for pid, tr in by_id.items():
            if tr["teamId"] != team_id:
                continue
            out.append({
                "id": pid, "name": tr["name"],
                "waypoints": [[round(w[0], 2), round(w[1], 2), round(w[2], 2)] for w in tr["waypoints"]],
                "receptions": [{"t_pass": round(r["t_pass"], 2), "t_receive": round(r["t_receive"], 2),
                               "end_x": round(r["end_x"], 2), "end_y": round(r["end_y"], 2)} for r in tr["receptions"]],
                "enter": tr["enter"], "exit": tr["exit"],
                "smoothed": smoothed.get(str(pid)),
            })
        return out

    events_out = []
    for e in match["events"]:
        if e["type"] in ("Starting XI", "Tactical Shift"):
            continue
        ev = {"t": round(e["t"], 2), "minute": e["minute"], "second": e["second"], "period": e["period"],
              "type": e["type"], "teamId": e["teamId"], "isHome": e["teamId"] == match["home"]["id"],
              "playerId": e.get("playerId"), "playerName": e.get("playerName"),
              "x": round(e["x"], 2) if e["x"] is not None else None,
              "y": round(e["y"], 2) if e["y"] is not None else None}
        if "passEnd" in e and e["passEnd"]:
            ev["endX"], ev["endY"] = round(e["passEnd"][0], 2), round(e["passEnd"][1], 2)
            ev["passOutcome"] = e.get("passOutcome")
        if e["type"] == "Shot":
            ev["isShot"] = True
            ev["shotOutcome"] = e.get("shotOutcome")
            ev["isGoal"] = e.get("shotOutcome") == "Goal"
            if e.get("shotEnd"):
                ev["endX"], ev["endY"] = round(e["shotEnd"][0], 2), round(e["shotEnd"][1], 2)
        if e.get("card"):
            ev["card"] = e["card"]
        events_out.append(ev)

    payload = {
        "matchId": match["matchId"], "date": match["date"], "stadium": match["stadium"], "referee": match["referee"],
        "pitchLengthM": PITCH_X_M, "pitchWidthM": PITCH_Y_M,
        "home": {"id": match["home"]["id"], "name": match["home"]["name"], "score": match["home"]["score"],
                 "tracks": team_payload(match["home"]["id"], home_s)},
        "away": {"id": match["away"]["id"], "name": match["away"]["name"], "score": match["away"]["score"],
                 "tracks": team_payload(match["away"]["id"], away_s)},
        "events": events_out,
        "maxT": round(match["maxT"], 2),
        "stoppages": [{"start": round(s["start"], 2), "end": round(s["end"], 2), "label": s["label"]} for s in stoppages],
        "calibration": {"k50": 1.224, "k68": 1.681, "k90": 2.773},
    }
    out_path = os.path.join(OUTPUT_DIR, "data", f"{match_id}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    return out_path, len(json.dumps(payload))

if __name__ == "__main__":
    mid = sys.argv[1] if len(sys.argv) > 1 else "3754348"
    path, size = build(mid)
    print(f"wrote {path} ({size/1e6:.2f} MB)")
