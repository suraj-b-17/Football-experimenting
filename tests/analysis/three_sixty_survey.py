"""Read-only survey: which StatsBomb open-data competitions have real 360
data, how much, and how dense the resulting player-position coverage is.
No fixes, no changes to the pipeline."""
import json
import os
import sys
import time
import urllib.request

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "football_anim", "survey360")
os.makedirs(CACHE, exist_ok=True)
RAW = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
API = "https://api.github.com/repos/statsbomb/open-data/contents/data/three-sixty"


def fetch(url, dest, is_json=True):
    if not os.path.exists(dest):
        req = urllib.request.Request(url, headers={"User-Agent": "research"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
    with open(dest, "rb") as f:
        raw = f.read()
    return json.loads(raw) if is_json else raw


def api_listing():
    """Directory listing of data/three-sixty/ via the GitHub API (paginated)."""
    dest = os.path.join(CACHE, "listing.json")
    if os.path.exists(dest):
        return json.load(open(dest, encoding="utf-8"))
    names = set()
    page = 1
    while True:
        req = urllib.request.Request(f"{API}?page={page}&per_page=100", headers={"User-Agent": "research"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                items = json.loads(r.read())
        except Exception as e:
            print("API listing failed:", e, "- falling back to per-match checks")
            return None
        if not items or "message" in items:
            break
        names.update(i["name"] for i in items if i["name"].endswith(".json"))
        if len(items) < 100:
            break
        page += 1
        time.sleep(0.2)
    json.dump(sorted(names), open(dest, "w", encoding="utf-8"))
    return sorted(names)


if __name__ == "__main__":
    comps = fetch(f"{RAW}/competitions.json", os.path.join(CACHE, "competitions.json"))
    has360 = [c for c in comps if c.get("match_available_360") is not None]
    print(f"{len(has360)} competition-seasons flag match_available_360 not null\n")

    listing = api_listing()
    if listing is not None:
        print(f"GitHub API: {len(listing)} files actually present under data/three-sixty/\n")

    summary = []
    for c in has360:
        cid, sid = c["competition_id"], c["season_id"]
        name = f"{c['competition_name']} {c['season_name']}"
        matches_path = os.path.join(CACHE, f"matches_{cid}_{sid}.json")
        try:
            matches = fetch(f"{RAW}/matches/{cid}/{sid}.json", matches_path)
        except Exception as e:
            print(f"{name}: FAILED to fetch match list ({e})")
            continue
        flagged = [m for m in matches if m.get("match_status_360") == "available"]
        verified = []
        for m in flagged:
            mid = m["match_id"]
            if listing is not None:
                ok = f"{mid}.json" in listing
            else:
                dest = os.path.join(CACHE, f"360_{mid}.json")
                try:
                    fetch(f"{RAW}/three-sixty/{mid}.json", dest)
                    ok = os.path.getsize(dest) > 50
                except Exception:
                    ok = False
            if ok:
                verified.append(mid)
        print(f"{name} (comp={cid},season={sid}): {len(matches)} matches, "
              f"{len(flagged)} flagged status_360=available, {len(verified)} verified (360 file actually present)")
        summary.append({"cid": cid, "sid": sid, "name": name, "n_matches": len(matches),
                        "n_flagged": len(flagged), "n_verified": len(verified), "verified_ids": verified})
    json.dump(summary, open(os.path.join(CACHE, "summary.json"), "w", encoding="utf-8"), indent=1)
