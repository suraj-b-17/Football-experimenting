"""Read-only, no fixes. For the top-3 360 competitions by match count, and
2015/16 Premier League for comparison: visible-player-per-frame stats, event/
frame alignment, and named-player real-position gap statistics.

Key fact, confirmed directly from the raw JSON (not assumed): a 360
freeze_frame entry carries NO player identity — only teammate/actor/keeper
booleans and a location. Only the event's own "actor" is a named player;
every other visible dot is anonymous. So a true per-NAMED-player gap
statistic from 360 is only computable for the actor, which is identical to
the ordinary event-based gap already available without 360 at all. This
script reports that named-player statistic (actor-only, same method for every
competition including PL 2015/16, for a clean comparison), and separately a
team-level "visible coverage" statistic from the anonymous entries, clearly
labelled as anonymous/not attributable to a specific player.
"""
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "football_anim", "survey360")
RAW = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
NON_POS = {"Starting XI", "Half Start", "Half End", "Substitution", "Tactical Shift", "Injury Stoppage",
          "Player Off", "Player On", "Bad Behaviour", "Referee Ball-Drop"}

TOP3 = [(43, 106, "FIFA World Cup 2022"), (72, 107, "Women's World Cup 2023"), (55, 282, "UEFA Euro 2024")]
SAMPLE_N = 20
SEED = 0


def fetch(url, dest):
    if not os.path.exists(dest):
        req = urllib.request.Request(url, headers={"User-Agent": "research"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
    return dest


def load_events(mid):
    p = fetch(f"{RAW}/events/{mid}.json", os.path.join(CACHE, f"events_{mid}.json"))
    return json.load(open(p, encoding="utf-8"))


def load_360(mid):
    p = os.path.join(CACHE, f"360_{mid}.json")
    if not os.path.exists(p) or os.path.getsize(p) < 20:
        return None
    return json.load(open(p, encoding="utf-8"))


def hms(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def named_player_gaps(events):
    """Same simple method for every competition: consecutive real (located,
    named-player) event timestamps per player, continuous match clock."""
    period_end = {}
    for e in events:
        period_end[e["period"]] = max(period_end.get(e["period"], 0.0), hms(e["timestamp"]))
    offset = {1: 0.0}
    for p in sorted(period_end):
        if p > 1:
            offset[p] = offset[p - 1] + period_end[p - 1]
    by_player = {}
    for e in events:
        if e["type"]["name"] in NON_POS or not e.get("location") or not e.get("player"):
            continue
        t = offset.get(e["period"], 0.0) + hms(e["timestamp"])
        by_player.setdefault(e["player"]["id"], []).append(t)
    gaps = []
    for ts in by_player.values():
        ts.sort()
        gaps += list(np.diff(ts))
    return np.array(gaps), by_player


def analyze_match(mid):
    events = load_events(mid)
    fr = load_360(mid)
    gaps, by_player = named_player_gaps(events)
    ev_with_loc = [e for e in events if e["type"]["name"] not in NON_POS and e.get("location")]
    ev_ids = {e["id"] for e in ev_with_loc}
    out = {"mid": mid, "gaps": gaps, "n_events_locatable": len(ev_with_loc)}
    if fr is not None:
        out["n_frames"] = len(fr)
        out["visible_counts"] = np.array([len(f.get("freeze_frame", [])) for f in fr])
        frame_ids = {f["event_uuid"] for f in fr}
        out["events_with_frame"] = len(ev_ids & frame_ids)
        # anonymous "any teammate visible" coverage: per team per frame, count teammates
        tm_counts = np.array([sum(1 for x in f.get("freeze_frame", []) if x.get("teammate")) for f in fr])
        out["teammate_visible_counts"] = tm_counts
    return out


def coverage_5s(gaps_per_player_ts, span):
    """share of player-seconds within 5s of a real timestamp, per player, averaged."""
    shares = []
    for ts in gaps_per_player_ts:
        if len(ts) < 2:
            continue
        ts = np.sort(np.array(ts))
        grid = np.arange(0, span, 1.0)
        idx = np.searchsorted(ts, grid)
        idx = np.clip(idx, 1, len(ts) - 1)
        d = np.minimum(np.abs(ts[idx] - grid), np.abs(ts[np.clip(idx - 1, 0, None)] - grid))
        shares.append((d <= 5.0).mean())
    return float(np.mean(shares)) if shares else np.nan


def report(name, results):
    all_gaps = np.concatenate([r["gaps"] for r in results if len(r["gaps"])])
    print(f"\n=== {name} ({len(results)} matches) ===")
    print(f"named-player gap (s): median {np.median(all_gaps):.2f}  p90 {np.percentile(all_gaps, 90):.2f}  n={len(all_gaps)}")
    has_frames = [r for r in results if "n_frames" in r]
    if has_frames:
        vc = np.concatenate([r["visible_counts"] for r in has_frames])
        tmc = np.concatenate([r["teammate_visible_counts"] for r in has_frames])
        tot_frames = sum(r["n_frames"] for r in has_frames)
        tot_loc = sum(r["n_events_locatable"] for r in has_frames)
        tot_matched = sum(r["events_with_frame"] for r in has_frames)
        print(f"360: {tot_frames} frames across {len(has_frames)} matches with 360")
        print(f"visible players/frame: mean {vc.mean():.2f}  std {vc.std():.2f}  min {vc.min()}  max {vc.max()}")
        print(f"(of which tagged teammate, anonymous): mean {tmc.mean():.2f}  std {tmc.std():.2f}")
        print(f"share of locatable events with a matching 360 frame: {100*tot_matched/tot_loc:.1f}% ({tot_matched}/{tot_loc})")
    return all_gaps


if __name__ == "__main__":
    # PL 2015/16 comparison, reusing the already-cached full season
    pl_cache = os.path.join(os.path.expanduser("~"), ".cache", "football_anim", "statsbomb", "events")
    pl_ids = [f[:-5] for f in os.listdir(pl_cache) if f.endswith(".json")]
    rng = np.random.default_rng(SEED)
    pl_sample = rng.choice(pl_ids, size=min(SAMPLE_N, len(pl_ids)), replace=False)
    pl_results = []
    for mid in pl_sample:
        events = json.load(open(os.path.join(pl_cache, f"{mid}.json"), encoding="utf-8"))
        gaps, _ = named_player_gaps(events)
        pl_results.append({"mid": mid, "gaps": gaps, "n_events_locatable": 0})
    report("2015/16 Premier League (StatsBomb, no 360)", pl_results)

    for cid, sid, name in TOP3:
        matches = json.load(open(os.path.join(CACHE, f"matches_{cid}_{sid}.json"), encoding="utf-8"))
        ids = [m["match_id"] for m in matches if m.get("match_status_360") == "available"]
        sample = list(rng.choice(ids, size=min(SAMPLE_N, len(ids)), replace=False))
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(analyze_match, sample))
        gaps = report(name, results)
        # 5s coverage estimate, named-player only (since only actor is identified)
        by_player_all = {}
        for mid in sample:
            events = load_events(mid)
            period_end = {}
            for e in events:
                period_end[e["period"]] = max(period_end.get(e["period"], 0.0), hms(e["timestamp"]))
            offset = {1: 0.0}
            for p in sorted(period_end):
                if p > 1:
                    offset[p] = offset[p - 1] + period_end[p - 1]
            span = offset.get(max(period_end), 0.0) + period_end.get(max(period_end), 0.0)
            _, bp = named_player_gaps(events)
            cov = coverage_5s(bp.values(), span)
            by_player_all[mid] = cov
        covs = [v for v in by_player_all.values() if not np.isnan(v)]
        print(f"named-player share of player-seconds within 5s of a real position: mean {100*np.mean(covs):.1f}% across {len(covs)} matches")
