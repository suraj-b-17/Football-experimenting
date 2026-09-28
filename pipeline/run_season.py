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
    """output/index.html: the match list embedded in the page, relative links,
    local stylesheet only — works by double-clicking, offline, from any folder."""
    import html
    rows = "\n".join(
        f'<tr class="hover:bg-gray-800"><td class="px-3 py-2 text-gray-400">{html.escape(info["match_date"])}</td>'
        f'<td class="px-3 py-2 text-right"><a class="hover:underline" href="viewer.html?match={mid}#match={mid}">'
        f'{html.escape(info["home_team"]["home_team_name"])}</a></td>'
        f'<td class="px-3 py-2 text-center font-mono font-bold"><a href="viewer.html?match={mid}#match={mid}">'
        f'{info["home_score"]} - {info["away_score"]}</a></td>'
        f'<td class="px-3 py-2"><a class="hover:underline" href="viewer.html?match={mid}#match={mid}">'
        f'{html.escape(info["away_team"]["away_team_name"])}</a></td></tr>'
        for mid, info in sorted(built, key=lambda b: b[1]["match_date"])
    )
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Premier League 2015/16 — match replays</title>
<link rel="stylesheet" href="viewer.css">
<style>body{{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#0B0F19;color:#E2E8F0}} a{{color:inherit}}</style>
</head><body class="p-6 max-w-3xl mx-auto">
<h1 class="text-xl font-bold mb-1">Premier League 2015/16</h1>
<p class="text-sm text-gray-400 mb-4">{len(built)} matches. Click a match to open its replay. Data: StatsBomb Open Data
(non-commercial research use — see LICENSES.md). Player positions between real events are a statistical
reconstruction — see BENCHMARKS.md.</p>
<table class="w-full text-sm border border-gray-800 rounded-lg overflow-hidden">
<tbody>{rows}</tbody></table></body></html>"""
    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def write_site(built):
    import shutil
    build_index(built)
    viewer_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "viewer")
    for f in ("viewer.html", "app.js", "viewer.css"):
        shutil.copy(os.path.join(viewer_dir, f), os.path.join(OUTPUT_DIR, f))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition-id", type=int, default=2)
    ap.add_argument("--season-id", type=int, default=27)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--matches", nargs="*", default=None)
    ap.add_argument("--site-only", action="store_true", help="rewrite index.html + viewer files for the payloads already built")
    args = ap.parse_args()
    if args.site_only:
        have = {f[:-3] for f in os.listdir(os.path.join(OUTPUT_DIR, "data")) if f.endswith(".js")}
        write_site([(mid, info) for mid, info in all_match_ids(args.competition_id, args.season_id) if mid in have])
        print(f"site written for {len(have)} matches")
        sys.exit(0)

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

    write_site(built)
    print(f"\n{len(built)} built, {len(failed)} failed (viewer copied to output/)")
    if failed:
        print("failed match_ids:", [m for m, _ in failed])
