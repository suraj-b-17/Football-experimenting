"""Summarise a frame dump (tests/drivers/frame_dump.js output) for the evidence
tables: per mode and team, deepest outfield player, offside line (second-last
defender incl. keeper), the two most advanced attackers relative to the
opponents' offside line, team length and width (outfield). All in each team's
OWN frame (x = metres from own goal line)."""
import json
import sys

L = 105.0


def summarise(path, gk_names):
    d = json.load(open(path, encoding="utf-8"))
    out = {}
    for mode in ("realism", "tactical"):
        rows = [r for r in d["rows"] if r["mode"] == mode]
        teams = sorted({r["team"] for r in rows})
        res = {}
        for tm in teams:
            mine = [r for r in rows if r["team"] == tm]
            opp = [r for r in rows if r["team"] != tm]
            of = [r for r in mine if r["name"] not in gk_names]
            xs = sorted(r["own"][0] for r in of)
            ys = sorted(r["own"][1] for r in of)
            opp_in_my_frame = sorted(L - r["own"][0] for r in opp)          # includes keeper
            line = opp_in_my_frame[-2] if len(opp_in_my_frame) >= 2 else None
            adv = sorted(of, key=lambda r: -r["own"][0])[:2]
            res[tm] = {"deepest_outfield_x": round(xs[0], 1), "team_length_m": round(xs[-1] - xs[0], 1),
                       "team_width_m": round(ys[-1] - ys[0], 1), "offside_line_x": round(line, 1) if line else None,
                       "two_most_advanced": [(r["name"], round(r["own"][0], 1), round(r["own"][0] - line, 1) if line else None) for r in adv]}
        out[mode] = res
    return out


if __name__ == "__main__":
    gks = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else set()
    print(json.dumps(summarise(sys.argv[1], gks), indent=1, ensure_ascii=False))
