"""Builds one match's viewer payload: output/data/<match_id>.json."""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import OUTPUT_DIR
from loader_statsbomb import load_match, PITCH_X_M, PITCH_Y_M
from build_match import detect_stoppages, dismissals
from apply_to_match import apply, bundle
from common import ballpath
from payload_io import compact, to_js
from common.features import role_from_position_name

KIND_CODE = {"event": 0, "carry_end": 1, "reception": 2, "freeze": 3, "event_offcam": 4}
MAX_SPEED_MPS = 9.5


def r2(a):
    return [round(float(v), 2) for v in a]


def build(match_id, competition_id=2, season_id=27, out_dir=None):
    match = load_match(match_id, competition_id, season_id)
    tracks, smoothed, bev, kicks = apply(match)
    B = bundle()
    stoppages = detect_stoppages(match)
    anomalies = list(match.get("_collisions", [])) + list(match.get("_timestamp_anomalies", []))

    carries_by_pid = {}
    for e in match["events"]:
        if e["type"] == "Carry" and e.get("carryEnd") and e.get("playerId") is not None:
            ln = float(np.hypot(e["carryEnd"][0] - e["x"], e["carryEnd"][1] - e["y"]))
            v = ln / e["duration"] if e["duration"] > 0 else float("inf")
            anomaly = v > MAX_SPEED_MPS
            if anomaly:
                anomalies.append({"type": "carry_speed", "t": round(e["t"], 3), "playerId": e["playerId"],
                                  "len_m": round(ln, 2), "duration_s": round(e["duration"], 3),
                                  "implied_mps": round(v, 1) if np.isfinite(v) else None})
            carries_by_pid.setdefault(e["playerId"], []).append(
                [round(e["t"], 3), round(e["t"] + e["duration"], 3), round(e["x"], 2), round(e["y"], 2),
                 round(e["carryEnd"][0], 2), round(e["carryEnd"][1], 2), int(anomaly)])

    def team_payload(team_id):
        out = []
        for tr in tracks:
            if tr["teamId"] != team_id:
                continue
            sm = smoothed[tr["id"]]
            first_role = role_from_position_name(tr["roles"][0][1]) if tr["roles"] else "MID"
            A = sorted(tr["anchors"], key=lambda a: a[0])
            for a, b in zip(A, A[1:]):
                dt, d = b[0] - a[0], float(np.hypot(b[1] - a[1], b[2] - a[2]))
                if d > 1.0 and (dt <= 0 or d / dt > MAX_SPEED_MPS) and "freeze" not in (a[3], b[3]):
                    anomalies.append({"type": "anchor_pair_speed", "t": round(a[0], 3), "playerId": tr["id"],
                                      "dt_s": round(dt, 3), "dist_m": round(d, 2)})
            out.append({
                "id": tr["id"], "name": tr["name"], "role": first_role,
                "intervals": [[round(a, 3), round(b, 3)] for a, b in tr["intervals"]],
                "anchors": [[round(a[0], 3), round(a[1], 2), round(a[2], 2), KIND_CODE[a[3]]] for a in A],
                "receptions": [{"t_pass": round(r["t_pass"], 3), "t_receive": round(r["t_receive"], 3),
                                "end_x": round(r["end_x"], 2), "end_y": round(r["end_y"], 2)} for r in tr["receptions"]],
                "carries": carries_by_pid.get(tr["id"], []),
                "kickoff": sm["kickoff_anchors"],
                "segs": [{"t": [round(float(v), 3) for v in s["t"]], "x": r2(s["x"]), "y": r2(s["y"]), "sd": r2(s["sd"])}
                         for s in sm["segments"]],
            })
        return out

    events_out = []
    for e in match["events"]:
        if e["type"] in ("Starting XI", "Tactical Shift"):
            continue
        ev = {"t": round(e["t"], 3), "dur": round(e["duration"], 3), "minute": e["minute"], "second": e["second"],
              "period": e["period"], "type": e["type"], "teamId": e["teamId"], "isHome": e["teamId"] == match["home"]["id"],
              "playerId": e.get("playerId"), "playerName": e.get("playerName"),
              "x": round(e["x"], 2) if e["x"] is not None else None,
              "y": round(e["y"], 2) if e["y"] is not None else None}
        end = e.get("passEnd") or e.get("carryEnd") or e.get("shotEnd")
        if end:
            ev["endX"], ev["endY"] = round(end[0], 2), round(end[1], 2)
        if e["type"] == "Pass":
            ev["passOutcome"] = e.get("passOutcome")
            if e.get("passHeight"):
                ev["passHeight"] = e["passHeight"]
        if e.get("receiptOutcome"):
            ev["receiptOutcome"] = e["receiptOutcome"]
        if e.get("gkType"):
            ev["gkType"] = e["gkType"]
        if e["type"] == "Shot":
            ev["isShot"] = True
            ev["shotOutcome"] = e.get("shotOutcome")
            ev["isGoal"] = e.get("shotOutcome") == "Goal"
        if e.get("card"):
            ev["card"] = e["card"]
        events_out.append(ev)

    T, X, Y = ballpath.build_path(bev)
    cal = B["calibration"]
    payload = {
        "matchId": match["matchId"], "date": match["date"], "stadium": match["stadium"], "referee": match["referee"],
        "pitchLengthM": PITCH_X_M, "pitchWidthM": PITCH_Y_M,
        "periods": [{"period": p, "start": round(a, 3), "end": round(b, 3)} for p, (a, b) in match["periodBounds"].items()],
        "home": {"id": match["home"]["id"], "name": match["home"]["name"], "score": match["home"]["score"],
                 "tracks": team_payload(match["home"]["id"])},
        "away": {"id": match["away"]["id"], "name": match["away"]["name"], "score": match["away"]["score"],
                 "tracks": team_payload(match["away"]["id"])},
        "events": events_out,
        "ball": {"frame": "home", "t": [round(float(v), 3) for v in T], "x": r2(X), "y": r2(Y)},
        "maxT": round(match["maxT"], 3),
        "kickoffs": [[round(t, 3), p] for t, p, _ in kicks],
        "stoppages": [{"start": round(s["start"], 3), "outT": round(s["out_t"], 3), "end": round(s["end"], 3),
                       "label": s["label"], "kind": s["kind"], "isHome": s["isHome"], "playerId": s["playerId"],
                       "x": None if s["x"] is None else round(s["x"], 2),
                       "y": None if s["y"] is None else round(s["y"], 2)} for s in stoppages],
        "dismissals": [{"t": round(t, 3), "playerId": pid, "card": c, "eventType": typ} for t, pid, c, typ in dismissals(match)],
        "anomalies": anomalies,
        "calibration": {"k50": round(cal["k50"], 3), "k68": round(cal["k68"], 3), "k90": round(cal["k90"], 3)},
        "model": B["name"],
    }
    out_path = os.path.join(out_dir or os.path.join(OUTPUT_DIR, "data"), f"{match_id}.js")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    s = to_js(match_id, compact(payload))
    with open(out_path, "w", encoding="ascii") as f:
        f.write(s)
    return out_path, len(s)


if __name__ == "__main__":
    mid = sys.argv[1] if len(sys.argv) > 1 else "3754129"
    path, size = build(mid)
    print(f"wrote {path} ({size/1e6:.2f} MB)")
