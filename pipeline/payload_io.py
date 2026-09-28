"""Payload file format: output/data/<match_id>.js, a plain script that sets
window.MATCH_DATA_<match_id> = {...}. Script tags load from file://, so the
viewer works by double-clicking index.html (fetch() of a .json does not).

Size: reconstruction knots (segs x/y/sd) and the ball path are rounded to 0.1 m
(each value changes by at most 0.05 m); integral timestamps are written as
integers. Real anchors, event locations, carries and receptions keep 0.01 m.
"""
import json
import re

POS_DECIMALS = 1


def _num(v, nd):
    r = round(float(v), nd)
    return int(r) if r == int(r) else r


def compact(payload):
    p = dict(payload)
    for side in ("home", "away"):
        team = dict(p[side]); tracks = []
        for tr in team["tracks"]:
            tr = dict(tr)
            tr["segs"] = [{"t": [_num(v, 3) for v in s["t"]], "x": [_num(v, POS_DECIMALS) for v in s["x"]],
                           "y": [_num(v, POS_DECIMALS) for v in s["y"]], "sd": [_num(v, POS_DECIMALS) for v in s["sd"]]}
                          for s in tr["segs"]]
            tracks.append(tr)
        team["tracks"] = tracks; p[side] = team
    b = p["ball"]
    p["ball"] = {"frame": b["frame"], "t": [_num(v, 3) for v in b["t"]], "x": [_num(v, POS_DECIMALS) for v in b["x"]],
                 "y": [_num(v, POS_DECIMALS) for v in b["y"]]}
    return p


def max_position_change(before, after):
    worst = 0.0
    for side in ("home", "away"):
        for a, b in zip(before[side]["tracks"], after[side]["tracks"]):
            for sa, sb in zip(a["segs"], b["segs"]):
                for k in ("x", "y"):
                    worst = max(worst, max((abs(u - v) for u, v in zip(sa[k], sb[k])), default=0))
            for aa, ab in zip(a["anchors"], b["anchors"]):
                worst = max(worst, abs(aa[1] - ab[1]), abs(aa[2] - ab[2]))
    for k in ("x", "y"):
        worst = max(worst, max(abs(u - v) for u, v in zip(before["ball"][k], after["ball"][k])))
    return worst


def to_js(match_id, payload):
    if not re.fullmatch(r"\d+", str(match_id)):
        raise ValueError(f"match id must be numeric: {match_id!r}")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return f"window.MATCH_DATA_{match_id}={body};\n"


def read_js(path):
    s = open(path, encoding="ascii").read()
    return json.loads(s[s.index("=") + 1: s.rstrip().rindex(";")])
