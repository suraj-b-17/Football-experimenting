"""Per-player anchors, on-pitch intervals and roles from one loaded StatsBomb
match (loader_statsbomb.load_match), plus ball events, kickoffs and stoppages.

Anchor kinds (all real StatsBomb information for a named player):
  event      location of an event the player performed
  carry_end  carry.end_location at t + duration
  reception  pass.end_location at t_pass + duration for the recipient of a
             completed pass, only when the recipient has no event of their own
             within 0.5 s of that arrival (otherwise it duplicates Ball Receipt)
  freeze     the player's position in a shot freeze frame
Kickoff soft anchors (templates, not observations) are added in apply_to_match.
"""
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.features import role_from_position_name

NON_POSITIONAL = {"Starting XI", "Half Start", "Half End", "Substitution", "Tactical Shift",
                  "Injury Stoppage", "Player Off", "Player On", "Bad Behaviour"}
BALL_EVENT_TYPES = {"Pass", "Ball Receipt*", "Carry", "Shot", "Clearance", "Ball Recovery",
                    "Interception", "Dribble", "Miscontrol", "Goal Keeper", "Block", "Duel",
                    "Dispossessed", "50/50", "Foul Won", "Referee Ball-Drop", "Shield"}
DISMISSAL_CARDS = {"Red Card", "Second Yellow"}
PITCH_X_M, PITCH_Y_M = 105.0, 68.0
MERGE_S = 0.05          # anchors of one player closer than this in time are a collision
SAME_PLACE_M = 0.05  # true float/tagging noise on a literal duplicate touch, not a real short movement
MAX_SPEED_MPS = 9.5


def dismissals(match):
    return [(e["t"], e["playerId"], e["card"], e["type"]) for e in match["events"]
            if e.get("card") in DISMISSAL_CARDS and e.get("playerId") is not None]


def _intervals(match, starters, team_of):
    """On-pitch intervals per player from Starting XI, Substitution, dismissal
    cards (on any event type) and Player Off / Player On."""
    max_t = match["maxT"]
    open_at = {pid: 0.0 for pid in starters}
    closed = {}          # pid -> list of [a, b]
    last_close = {}      # pid -> "off" | "final"

    def close(pid, t, why):
        if pid in open_at:
            closed.setdefault(pid, []).append([open_at.pop(pid), t])
        if why == "final" or pid in closed:
            last_close[pid] = why if why == "final" or last_close.get(pid) != "final" else "final"

    for e in sorted(match["events"], key=lambda e: e["t"]):
        pid, t = e.get("playerId"), e["t"]
        if pid is None:
            continue
        if e["type"] == "Substitution":
            close(pid, t, "final")
            rep = e.get("subReplacementId")
            if rep is not None and rep not in open_at and last_close.get(rep) != "final":
                open_at[rep] = t
                team_of.setdefault(rep, e["teamId"])
        elif e.get("card") in DISMISSAL_CARDS:
            close(pid, t, "final")
        elif e["type"] == "Player Off":
            close(pid, t, "off")
        elif e["type"] == "Player On":
            if pid not in open_at and last_close.get(pid) == "off":
                open_at[pid] = t
    for pid, a in list(open_at.items()):
        closed.setdefault(pid, []).append([a, max_t])
    # same 3-decimal rounding as anchor times (D7): an anchor at the moment of a
    # substitution must fall inside the interval, not 1e-7 s after it
    return {pid: [[round(a, 3), round(b, 3)] for a, b in ivs] for pid, ivs in closed.items()}


def _roles(match):
    roles = {}
    for e in match["events"]:
        if e["type"] in ("Starting XI", "Tactical Shift"):
            for pl in e.get("tacticsLineup", []):
                roles.setdefault(pl["playerId"], []).append((e["t"], pl["positionName"]))
        elif e.get("playerId") is not None and e.get("positionName"):
            roles.setdefault(e["playerId"], []).append((e["t"], e["positionName"]))
    out = {}
    for pid, lst in roles.items():
        lst.sort(key=lambda r: r[0])
        comp = []
        for t, name in lst:
            if not comp or comp[-1][1] != name:
                comp.append([t, name])
        out[pid] = comp
    return out


def role_at(role_timeline, t):
    """StatsBomb position name in force at t (earliest known one before the first entry)."""
    if not role_timeline:
        return None
    ts = [r[0] for r in role_timeline]
    i = max(bisect.bisect_right(ts, t) - 1, 0)
    return role_timeline[i][1]


def build_tracks(match, use_freeze=True, freeze_exclude=frozenset()):
    team_of, starters = {}, []
    for e in match["events"]:
        if e["type"] == "Starting XI":
            for pl in e.get("tacticsLineup", []):
                starters.append(pl["playerId"]); team_of[pl["playerId"]] = e["teamId"]
    for e in match["events"]:
        if e.get("playerId") is not None and e.get("teamId") is not None:
            team_of.setdefault(e["playerId"], e["teamId"])

    anchors = {}

    def add(pid, t, x, y, kind, order):
        anchors.setdefault(pid, []).append((t, x, y, kind, order))

    own_event_ts = {}
    for e in match["events"]:
        pid = e.get("playerId")
        if pid is None or e["type"] in NON_POSITIONAL or e["x"] is None:
            continue
        kind = "event_offcam" if e.get("offCamera") else "event"
        add(pid, e["t"], e["x"], e["y"], kind, e["index"])
        own_event_ts.setdefault(pid, []).append(e["t"])
        if e.get("carryEnd"):
            add(pid, e["t"] + e["duration"], e["carryEnd"][0], e["carryEnd"][1], "carry_end", e["index"] + 0.5)
            own_event_ts[pid].append(e["t"] + e["duration"])
    for v in own_event_ts.values():
        v.sort()

    receptions = {}
    for e in match["events"]:
        rid = e.get("passRecipientId")
        if e["type"] != "Pass" or e.get("passOutcome") != "Complete" or rid is None or not e.get("passEnd"):
            continue
        t_arr = e["t"] + e["duration"]
        receptions.setdefault(rid, []).append({"t_pass": e["t"], "t_receive": t_arr,
                                               "end_x": e["passEnd"][0], "end_y": e["passEnd"][1]})
        ts = own_event_ts.get(rid, [])
        i = bisect.bisect_left(ts, t_arr - 0.5)
        if not (i < len(ts) and ts[i] <= t_arr + 0.5):
            add(rid, t_arr, e["passEnd"][0], e["passEnd"][1], "reception", e["index"] + 0.6)

    n_freeze = n_freeze_unmatched = 0
    if use_freeze:
        for e in match["events"]:
            if e["type"] != "Shot" or e["id"] in freeze_exclude:
                continue
            for ff in e.get("freezeFrame") or []:
                pid = ff.get("playerId")
                if pid is None:
                    n_freeze_unmatched += 1
                    continue
                x, y = (ff["x"], ff["y"]) if ff["teammate"] else (PITCH_X_M - ff["x"], PITCH_Y_M - ff["y"])
                add(pid, e["t"], x, y, "freeze", e["index"] + 0.7)
                n_freeze += 1

    intervals = _intervals(match, starters, team_of)
    roles = _roles(match)
    tracks = []
    collisions = []
    for pid, ivs in intervals.items():
        pts = sorted(anchors.get(pid, []), key=lambda a: (round(a[0], 3), a[4]))
        merged = resolve_collisions(pid, pts, collisions)
        # round once here: every consumer (smoother knots, payload, viewer) then
        # sees the identical timestamp for the same anchor
        tracks.append({
            "id": pid, "name": match["id2name"].get(pid, "Player"), "teamId": team_of.get(pid),
            "isHome": team_of.get(pid) == match["home"]["id"],
            "anchors": [[round(a[0], 3), a[1], a[2], a[3]] for a in merged],
            "receptions": sorted(receptions.get(pid, []), key=lambda r: r["t_pass"]),
            "intervals": ivs, "roles": roles.get(pid, []),
        })
    match["_freeze_stats"] = {"anchors": n_freeze, "no_player_id": n_freeze_unmatched}
    match["_collisions"] = collisions
    return tracks


def resolve_collisions(pid, pts, log):
    """Strictly increasing anchor times without moving any real location.

    Two passes, because a three-way cluster (a real short carry immediately
    followed by the next action starting exactly where the carry ended — a
    routine StatsBomb pattern) otherwise makes a genuinely distinct anchor
    un-refittable purely because two OTHER points are squeezed at the same
    instant (found via QA: a receipt, a 0.04s/0.3m carry, then a pass
    starting at the carry's own end point — the carry's end location was
    silently discarded with nowhere to fit, breaking the carry's on-screen
    path — CHANGELOG_fix.md D12).

    Pass 1 collapses genuine duplicate tags of the literal same touch
    (<= MERGE_S apart AND <= SAME_PLACE_M apart, ANY kind — e.g. Ball
    Receipt + Carry start, or a Carry's end_location and the very next event
    that starts there) into one point, kept at the earliest of the two times.
    Pass 2 re-times anything still too close in time, or whose implied speed
    is impossible, to the earliest physically possible moment (distance /
    MAX_SPEED_MPS after the previous point), or drops it if that still will
    not fit before the next point. Every case is logged.
    """
    dedup = []
    for a in pts:
        if dedup:
            dt0 = a[0] - dedup[-1][0]
            d0 = ((a[1] - dedup[-1][1]) ** 2 + (a[2] - dedup[-1][2]) ** 2) ** 0.5
            if dt0 <= MERGE_S and d0 <= SAME_PLACE_M:
                # Both agree on position exactly, so which one is dropped never
                # moves anything — except 'event_offcam' also carries a softer
                # Kalman observation variance downstream. If the discarded
                # duplicate was on-camera, keeping the off-camera tag needlessly
                # downgrades confidence in a position both sources confirm,
                # which left the reconstruction curve's knot ~1m off the real
                # (and fully trusted) value at that instant, visible as a
                # one-frame speed spike where the viewer's carry animation
                # (drawn straight from the real end_location) hands off to it
                # (CHANGELOG_fix.md D13). Keep the more confident tag.
                if dedup[-1][3] == "event_offcam" and a[3] != "event_offcam":
                    log.append({"type": "collision_duplicate_dropped", "playerId": pid, "t": round(dedup[-1][0], 3),
                                "dist_m": round(d0, 3), "kind": dedup[-1][3], "dt_s": round(dt0, 3)})
                    dedup[-1] = a
                else:
                    log.append({"type": "collision_duplicate_dropped", "playerId": pid, "t": round(a[0], 3),
                                "dist_m": round(d0, 3), "kind": a[3], "dt_s": round(dt0, 3)})
                continue
        dedup.append(a)

    out = []
    for i, a in enumerate(dedup):
        if out:
            dt = a[0] - out[-1][0]
            d = ((a[1] - out[-1][1]) ** 2 + (a[2] - out[-1][2]) ** 2) ** 0.5
            implied = d / max(dt, 1e-6)
        if out and (dt <= MERGE_S or implied > MAX_SPEED_MPS):
            prev = out[-1]
            t_new = prev[0] + max(d / MAX_SPEED_MPS, MERGE_S + 0.001)
            nxt = dedup[i + 1][0] if i + 1 < len(dedup) else float("inf")
            if t_new < nxt - MERGE_S:
                log.append({"type": "collision_retimed", "playerId": pid, "t": round(a[0], 3),
                            "t_new": round(t_new, 3), "dist_m": round(d, 2), "kind": a[3], "dt_s": round(dt, 3)})
                out.append((t_new, a[1], a[2], a[3], a[4]))
            else:
                log.append({"type": "collision_dropped", "playerId": pid, "t": round(a[0], 3),
                            "dist_m": round(d, 2), "kind": a[3], "dt_s": round(dt, 3)})
            continue
        out.append(a)
    return out


def ball_events(match):
    """On-ball events -> common.ballpath event dicts in the HOME frame."""
    home_id = match["home"]["id"]
    out = []
    for e in match["events"]:
        if e["type"] not in BALL_EVENT_TYPES or e["x"] is None or e.get("teamId") is None:
            continue
        x0, y0 = e["x"], e["y"]
        x1, y1, t1 = x0, y0, e["t"]
        end = e.get("passEnd") or e.get("carryEnd") or e.get("shotEnd")
        if end and e["type"] in ("Pass", "Carry", "Shot"):
            x1, y1, t1 = end[0], end[1], e["t"] + e["duration"]
        if e["teamId"] != home_id:
            x0, y0, x1, y1 = PITCH_X_M - x0, PITCH_Y_M - y0, PITCH_X_M - x1, PITCH_Y_M - y1
        out.append({"t0": e["t"], "x0": x0, "y0": y0, "t1": t1, "x1": x1, "y1": y1, "team": e["teamId"]})
    return out


def kickoffs(match):
    """(t, period, kicking team id) for every kickoff: period starts and restarts
    after goals. A kickoff is the first positional event of a possession whose
    play_pattern is 'From Kick Off'."""
    seen, out = set(), []
    for e in sorted(match["events"], key=lambda e: e["t"]):
        if e["type"] in NON_POSITIONAL or e["x"] is None:
            continue
        key = (e["period"], e.get("possession"))
        if key in seen:
            continue
        seen.add(key)
        if e.get("playPattern") == "From Kick Off":
            out.append((e["t"], e["period"], e.get("teamId")))
    return out


SET_PIECE_KIND = {"Throw-in": "throw_in", "Goal Kick": "goal_kick", "Corner": "corner", "Free Kick": "free_kick",
                  "Kick Off": "goal", "Penalty": "penalty"}
KIND_LABEL = {"throw_in": "Throw-in", "goal_kick": "Goal kick", "corner": "Corner", "free_kick": "Free kick",
              "foul": "Foul", "offside": "Offside", "goal": "Goal", "penalty": "Penalty"}
INCIDENT_LOOKBACK_S = 6.0


def detect_stoppages(match, min_gap_s=5.0):
    """Real dead-ball stoppages: a gap of at least min_gap_s ending in a
    set-piece restart, as StatsBomb types the restart event itself
    (loader_statsbomb.SET_PIECE_PASS / SET_PIECE_SHOT). One per gap, no merging:
    each has its own restart. A mid-period Kick Off only follows a goal (period
    starts are not gaps within a period).

    Every field is read off real events:
      start      the last positional event before the gap
      out_t      when the ball actually stopped being in play: that event's own
                 end (t + duration, e.g. a pass arriving out of play), capped at
                 the restart
      end        the restart event
      kind       throw_in / goal_kick / corner / free_kick / goal / penalty; a
                 free kick is refined to foul or offside when a Foul
                 Committed/Won, or an offside (a Pass with outcome "Pass
                 Offside", or an Offside event), happened in the last few
                 seconds before the gap
      isHome, x, y, playerId   the team, place (HOME frame) and player of the
                 restart event itself
    """
    evs = [e for e in match["events"] if e["type"] not in NON_POSITIONAL]
    evs.sort(key=lambda e: e["t"])
    out = []
    for i in range(1, len(evs)):
        before, after = evs[i - 1], evs[i]
        if after["period"] != before["period"]:
            continue
        gap = after["t"] - before["t"]
        if gap < min_gap_s or after.get("setPiece") not in SET_PIECE_KIND:
            continue
        kind = SET_PIECE_KIND[after["setPiece"]]
        if kind == "free_kick":
            recent = [e for e in evs[max(0, i - 6):i] if before["t"] - e["t"] <= INCIDENT_LOOKBACK_S]
            if any(e.get("passOutcome") == "Pass Offside" or e["type"] == "Offside" for e in recent):
                kind = "offside"
            elif any(e["type"] in ("Foul Committed", "Foul Won") for e in recent):
                kind = "foul"
        out_t = min(before["t"] + (before.get("duration") or 0.0), after["t"])
        x, y = after["x"], after["y"]
        if x is not None and not after["isHome"]:
            x, y = PITCH_X_M - x, PITCH_Y_M - y
        out.append({"start": before["t"], "out_t": out_t, "end": after["t"], "label": KIND_LABEL[kind], "kind": kind,
                    "isHome": after["isHome"], "x": x, "y": y, "playerId": after.get("playerId")})
    return out


if __name__ == "__main__":
    from loader_statsbomb import load_match
    m = load_match(sys.argv[1] if len(sys.argv) > 1 else "3754348")
    tr = build_tracks(m)
    print(len(tr), "tracks,", sum(len(t["anchors"]) for t in tr), "anchors,",
          len(kickoffs(m)), "kickoffs,", len(detect_stoppages(m)), "stoppages", m["_freeze_stats"])
    for t in tr:
        if len(t["intervals"]) > 1 or t["intervals"][-1][1] < m["maxT"] - 1:
            print(" ", t["name"], t["intervals"])
