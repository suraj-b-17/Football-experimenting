"""Loops build_payload.py over every match in a competition/season (default:
StatsBomb competition_id=2, season_id=27 — the full 2015/16 Premier League,
380 matches) and writes output/index.html linking to each one.

Usage:
  python run_season.py                 # all 380 matches
  python run_season.py --limit 10       # first 10 (smoke test)
  python run_season.py --matches 3754348 3754129   # specific match_ids only
"""
import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import OUTPUT_DIR, cache
from build_payload import build

REPO_RAW = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

def all_match_ids(competition_id, season_id):
    import urllib.request
    path = cache("statsbomb", "matches", f"{competition_id}_{season_id}.json")
    if not os.path.exists(path):
        urllib.request.urlretrieve(f"{REPO_RAW}/matches/{competition_id}/{season_id}.json", path)
    matches = json.load(open(path, encoding="utf-8"))
    return [(str(m["match_id"]), m) for m in matches]

def build_index(built):
    rows = "\n".join(
        f'<tr class="hover:bg-gray-800 cursor-pointer" onclick="location.href=\'viewer.html?match={mid}\'">'
        f'<td class="px-3 py-2 text-gray-400">{info["match_date"]}</td>'
        f'<td class="px-3 py-2 text-right">{info["home_team"]["home_team_name"]}</td>'
        f'<td class="px-3 py-2 text-center font-mono font-bold">{info["home_score"]} - {info["away_score"]}</td>'
        f'<td class="px-3 py-2">{info["away_team"]["away_team_name"]}</td></tr>'
        for mid, info in built
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Premier League 2015/16 — match animations</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{{font-family:Inter,sans-serif;background:#0B0F19;color:#E2E8F0}}</style>
</head><body class="p-6 max-w-3xl mx-auto">
<h1 class="text-xl font-bold mb-1">Premier League 2015/16</h1>
<p class="text-sm text-gray-400 mb-4">{len(built)} matches. Data: StatsBomb Open Data
(analysis/research use — see LICENSES.md). Reconstruction: see BENCHMARKS.md.</p>
<table class="w-full text-sm border border-gray-800 rounded-lg overflow-hidden">
<tbody>{rows}</tbody></table></body></html>"""
    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition-id", type=int, default=2)
    ap.add_argument("--season-id", type=int, default=27)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--matches", nargs="*", default=None)
    args = ap.parse_args()

    all_ids = all_match_ids(args.competition_id, args.season_id)
    if args.matches:
        wanted = set(args.matches)
        all_ids = [(mid, info) for mid, info in all_ids if mid in wanted]
    if args.limit:
        all_ids = all_ids[: args.limit]

    built, failed = [], []
    for i, (mid, info) in enumerate(all_ids):
        t0 = time.time()
        try:
            path, size = build(mid, args.competition_id, args.season_id)
            built.append((mid, info))
            print(f"[{i+1}/{len(all_ids)}] {mid}: {info['home_team']['home_team_name']} "
                  f"{info['home_score']}-{info['away_score']} {info['away_team']['away_team_name']} "
                  f"-> {size/1e6:.2f}MB ({time.time()-t0:.0f}s)", flush=True)
        except Exception as e:
            failed.append((mid, str(e)))
            print(f"[{i+1}/{len(all_ids)}] {mid}: FAILED — {e}", flush=True)
            traceback.print_exc()

    build_index(built)
    print(f"\n{len(built)} built, {len(failed)} failed")
    if failed:
        print("failed match_ids:", [m for m, _ in failed])
