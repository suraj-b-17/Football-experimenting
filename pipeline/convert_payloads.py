"""Regenerates existing output/data/<id>.json payloads as <id>.js (no model or
reconstruction re-run), verifies no position moved by more than 0.05 m,
removes the .json, and reports folder size before/after."""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import OUTPUT_DIR
from payload_io import compact, to_js, max_position_change, read_js

TOL = 0.05 + 1e-9


def one(fn):
    src = os.path.join(OUTPUT_DIR, "data", fn)
    mid = fn[:-5]
    before = json.load(open(src, encoding="utf-8"))
    after = compact(before)
    js = to_js(mid, after)
    dst = os.path.join(OUTPUT_DIR, "data", f"{mid}.js")
    with open(dst, "w", encoding="ascii") as f:
        f.write(js)
    worst = max_position_change(before, read_js(dst))
    if worst > TOL:
        raise RuntimeError(f"{mid}: position changed by {worst:.4f} m")
    size_before = os.path.getsize(src)
    os.remove(src)
    return mid, size_before, os.path.getsize(dst), worst


if __name__ == "__main__":
    files = sorted(f for f in os.listdir(os.path.join(OUTPUT_DIR, "data")) if f.endswith(".json"))
    if not files:
        print("no .json payloads to convert (build_payload.py already writes .js directly)")
        sys.exit(0)
    with ProcessPoolExecutor(8) as ex:
        res = list(ex.map(one, files))
    b = sum(r[1] for r in res); a = sum(r[2] for r in res)
    print(f"{len(res)} payloads converted; size {b/1e6:.0f} MB (.json) -> {a/1e6:.0f} MB (.js), "
          f"{100*(1-a/b):.0f}% smaller; largest position change {max(r[3] for r in res):.4f} m (limit 0.05 m)")
