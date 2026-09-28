"""Runs the automated QA driver (tests/drivers/qa.js, on the real viewer/app.js)
over every built match and writes output/qa_report.csv (one row per match) and
output/qa_flagged.json (every flagged item)."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from paths import OUTPUT_DIR


import re

# Things that break on file:// or need the network. CSS/JS comments may mention a
# URL (Tailwind's MIT licence comment does); only actual loads are flagged.
FORBIDDEN = [
    (re.compile(r"\bfetch\s*\("), "fetch()"),
    (re.compile(r"XMLHttpRequest"), "XMLHttpRequest"),
    (re.compile(r"\bimport\s*\(|^\s*import\s|<script[^>]*type=[\"']module", re.M), "ES module import"),
    (re.compile(r"new\s+(Shared)?Worker\s*\("), "Web Worker"),
    (re.compile(r"WebAssembly"), "WebAssembly"),
    (re.compile(r"(?:src|href)\s*=\s*[\"']?(?:https?:)?//", re.I), "external src/href"),
    (re.compile(r"url\(\s*[\"']?(?:https?:)?//|@import", re.I), "external CSS load"),
]


def static_check():
    """Fails on any fetch/XHR/module/worker/wasm or external load in the viewer,
    index or any payload file."""
    bad = []
    files = [os.path.join(OUTPUT_DIR, f) for f in ("index.html", "viewer.html", "app.js", "viewer.css")]
    files += [os.path.join(OUTPUT_DIR, "data", f) for f in os.listdir(os.path.join(OUTPUT_DIR, "data"))]
    for path in files:
        text = open(path, encoding="utf-8", errors="replace").read()
        if path.endswith(".js") and os.path.dirname(path).endswith("data") and re.search(r"https?:|//", text):
            bad.append((path, "URL in payload"))
        for rx, what in FORBIDDEN:
            if path.endswith(".css") and what == "external src/href":
                continue
            if rx.search(text):
                bad.append((path, what))
    return files, bad


def run_one(mid):
    r = subprocess.run(["node", os.path.join(ROOT, "tests", "viewer_harness.js"), mid,
                        os.path.join(ROOT, "tests", "drivers", "qa.js")], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        return {"matchId": mid, "qa_error": r.stderr[-500:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    files, bad = static_check()
    print(f"static check: {len(files)} files scanned, {len(bad)} problems")
    for b in bad:
        print("  STATIC FAIL", b)
    ids = sys.argv[1:] or sorted(f[:-3] for f in os.listdir(os.path.join(OUTPUT_DIR, "data")) if f.endswith(".js"))
    with ThreadPoolExecutor(12) as ex:
        rows = list(ex.map(run_one, ids))
    flagged = {r["matchId"]: {k: r.pop(k) for k in list(r) if k.endswith("_flagged") or k.endswith("_worst_frames") or k == "count_flags"} for r in rows}
    df = pd.DataFrame(rows)
    df["static_check_problems"] = len(bad)
    df.to_csv(os.path.join(OUTPUT_DIR, "qa_report.csv"), index=False)
    json.dump(flagged, open(os.path.join(OUTPUT_DIR, "qa_flagged.json"), "w"), indent=1)
    num = df.select_dtypes("number")
    print(f"{len(df)} matches; errors: {df['qa_error'].notna().sum() if 'qa_error' in df else 0}")
    print(num.sum().to_string())
    if bad:
        sys.exit(1)
