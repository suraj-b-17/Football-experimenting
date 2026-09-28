"""Builds per-player waypoint tracks, pass-reception anchors, and stoppage
windows from a loaded StatsBomb match (loader_statsbomb.load_match), built
from StatsBomb's schema, which gives real, explicit fields that a sparser
event feed would otherwise force to be inferred:
  - pass.recipient: the actual receiving player, not a guessed "next event by
    a different player" heuristic.
  - play_pattern: the actual restart type ("From Throw In" etc.) tagged by
    StatsBomb on the restart event itself, not inferred from qualifiers.
  - carry.end_location + duration: a real second waypoint (with a real
    elapsed time) for a dribble, not just its start.
"""
import bisect
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loader_statsbomb import RESTART_LABELS

NON_POSITIONAL = {"Starting XI", "Half Start", "Half End", "Substitution", "Tactical Shift", "Injury Stoppage"}
PITCH_X_M, PITCH_Y_M = 105.0, 68.0

def position_anchor(name):
    """A standard-formation-template (x, y) in meters for a named StatsBomb
    position (e.g. "Right Center Back") — used only as the ONE seed point
    before a player's first real touch, in the same team-own-attacking-
    direction frame the rest of the pipeline uses. Deliberately coarse (a
    real touch overrides it within seconds for almost every player); parsed
    from the name's own words rather than a hardcoded position-id table, so
    it can't silently mismatch if StatsBomb's id numbering differs by
    season."""
    n = name.lower()
    if "goalkeeper" in n:
        x = 0.04
    elif "back" in n and "wing" not in n:
        x = 0.18
    elif "wing back" in n:
        x = 0.28
    elif "defensive midfield" in n:
        x = 0.38
    elif "attacking midfield" in n:
        x = 0.68
    elif "midfield" in n:
        x = 0.5
    elif "wing" in n:
        x = 0.72
    else:  # forward / striker
        x = 0.85
    if n.startswith("right") or " right" in n:
        y = 0.18
    elif n.startswith("left") or " left" in n:
        y = 0.82
    else:
        y = 0.5
    return x * PITCH_X_M, y * PITCH_Y_M

def build_tracks(match):
    """One track per player: {id, name, teamId, waypoints:[(t,x,y),...],
    receptions:[{t_pass,t_receive,end_x,end_y}], enter, exit}."""
    tracks = {}

    def get(pid, team_id):
        if pid not in tracks:
            tracks[pid] = {"id": pid, "name": match["id2name"].get(pid, "Player"),
                           "teamId": team_id, "waypoints": [], "receptions": [],
                           "enter": None, "exit": None}
        return tracks[pid]

    # Seed starting XI at kickoff (t=0) so a player has an anchor before their
    # own first touch, and record real enter/exit from Substitution events.
    for e in match["events"]:
        if e["type"] == "Starting XI":
            for pl in e.get("tacticsLineup", []):
                tr = get(pl["playerId"], e["teamId"])
                tr["enter"] = 0.0
                # A standard-formation-template seed point at kickoff, so a
                # player has SOME anchor before their own first touch (which
                # can be many seconds in) — same reasoning as the old
                # project: a later teammate's substitution isn't a real
                # position reset for THIS player, so this is only seeded
                # once, at t=0, never re-injected at a formation change.
                ax, ay = position_anchor(pl["positionName"])
                tr["waypoints"].append((0.0, ax, ay))

    for e in match["events"]:
        pid, team_id, t = e.get("playerId"), e.get("teamId"), e["t"]
        if pid is None:
            continue
        if e["type"] == "Substitution":
            tr = get(pid, team_id)
            tr["exit"] = t
            rep_id = e.get("subReplacementId")
            if rep_id is not None:
                get(rep_id, team_id)["enter"] = t
            continue
        if e["type"] in ("Bad Behaviour",) and e.get("card") in ("Red Card", "Second Yellow"):
            get(pid, team_id)["exit"] = t
            continue
        if e["type"] in NON_POSITIONAL:
            continue
        if e["x"] is None:
            continue
        tr = get(pid, team_id)
        tr["waypoints"].append((t, e["x"], e["y"], None))
        # A Carry has a real second sample: where the dribble ended, at a
        # real elapsed time (carry.duration), not just its start.
        if "carryEnd" in e:
            tr["waypoints"].append((t + e.get("duration", 0.0), e["carryEnd"][0], e["carryEnd"][1], None))

        # Pass reception: a genuine ground-truth observation of a DIFFERENT
        # player (the recipient) at the pass's arrival instant — only for
        # actually-completed passes (StatsBomb tags a "recipient" even on
        # some incomplete/intercepted passes, meaning "who it was aimed at,"
        # not "who touched it"; using that here would inject a position the
        # ball never reached).
        if e.get("passOutcome") == "Complete" and e.get("passRecipientId") is not None and e.get("passEnd"):
            recv_tr = get(e["passRecipientId"], team_id)
            recv_tr["receptions"].append({
                "t_pass": t, "t_receive": None,  # filled in once we know the recipient's own next touch time
                "end_x": e["passEnd"][0], "end_y": e["passEnd"][1],
            })

    # A reception's t_receive is the receiving player's OWN next real touch
    # after the pass — matching the ball's own on-screen flight, which (per
    # the earlier project's tested design) only visually arrives at the next
    # real event, not at the pass's own timestamp.
    for tr in tracks.values():
        wp_times = sorted(w[0] for w in tr["waypoints"])
        for r in tr["receptions"]:
            i = bisect.bisect_right(wp_times, r["t_pass"])
            r["t_receive"] = wp_times[i] if i < len(wp_times) else r["t_pass"] + 3.0

    out = []
    for tr in tracks.values():
        real = list(tr["waypoints"])
        # de-dupe exact/near-duplicate (t,x,y) via dict.fromkeys (order-preserving
        # — a plain set() has no defined iteration order and would make a
        # same-timestamp collision's "last one wins" merge below effectively
        # random, silently picking whichever value the hash table happened
        # to visit last rather than the intended reception anchor).
        for r in tr["receptions"]:
            real.append((r["t_receive"], r["end_x"], r["end_y"]))
        real = sorted(dict.fromkeys(real), key=lambda w: w[0])
        cleaned = []
        for w in real:
            if cleaned and cleaned[-1][0] == w[0]:
                cleaned[-1] = w  # last-appended wins: receptions are appended after real touches above
            else:
                cleaned.append(w)
        if not cleaned:
            continue
        tr["waypoints"] = [list(w) for w in cleaned]
        tr["receptions"] = [r for r in tr["receptions"] if r["t_receive"] is not None]
        if tr["enter"] is None:
            tr["enter"] = cleaned[0][0]
        out.append(tr)
    return out

def detect_stoppages(match, min_gap_s=5.0):
    """Real dead-ball stoppages, read directly off StatsBomb's play_pattern
    tag on the restart event (see RESTART_LABELS) — no heuristic guessing."""
    evs = [e for e in match["events"] if e["type"] not in NON_POSITIONAL]
    evs.sort(key=lambda e: e["t"])
    out = []
    for i in range(1, len(evs)):
        before, after = evs[i - 1], evs[i]
        gap = after["t"] - before["t"]
        if gap < min_gap_s:
            continue
        label = RESTART_LABELS.get(after.get("playPattern"))
        if label == "Free kick" and before["type"] in ("Foul Committed", "Bad Behaviour"):
            label = "Foul — free kick"
        if label is None:
            continue
        out.append({"start": before["t"], "end": after["t"], "label": label})

    specific = set(RESTART_LABELS.values()) | {"Foul — free kick"}
    merged = []
    for s in out:
        if merged and s["start"] - merged[-1]["end"] <= 2.0:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
            if s["label"] in specific and merged[-1]["label"] not in specific:
                merged[-1]["label"] = s["label"]
        else:
            merged.append(dict(s))
    return merged

if __name__ == "__main__":
    from loader_statsbomb import load_match
    m = load_match(sys.argv[1] if len(sys.argv) > 1 else "3754348")
    tracks = build_tracks(m)
    stoppages = detect_stoppages(m)
    print(f"{len(tracks)} player tracks, {sum(len(t['waypoints']) for t in tracks)} total waypoints, "
          f"{sum(len(t['receptions']) for t in tracks)} pass receptions, {len(stoppages)} stoppages")
    from collections import Counter
    print(Counter(s["label"] for s in stoppages))
