"""Builds labelled contact sheets from browser screenshots
(tests/browser/phase_shots.js). Usage:
  python tests/analysis/contact_sheet.py <spec.json>
spec: {"out": path, "title": str, "cols": int, "width": px per tile,
       "tiles": [{"img": path, "label": str}]}"""
import json
import sys

from PIL import Image, ImageDraw, ImageFont

spec = json.load(open(sys.argv[1], encoding="utf-8"))
try:
    font, big = ImageFont.truetype("arial.ttf", 15), ImageFont.truetype("arialbd.ttf", 18)
except OSError:
    font = big = ImageFont.load_default()
tiles = [(Image.open(t["img"]), t["label"]) for t in spec["tiles"]]
cols, tw = spec.get("cols", 4), spec.get("width", 480)
w0, h0 = tiles[0][0].size
th = int(h0 * tw / w0)
rows = (len(tiles) + cols - 1) // cols
sheet = Image.new("RGB", (tw * cols, 34 + rows * (th + 24)), "#0B0F19")
d = ImageDraw.Draw(sheet)
d.text((8, 6), spec["title"], fill="white", font=big)
for i, (im, lab) in enumerate(tiles):
    x, y = (i % cols) * tw, 34 + (i // cols) * (th + 24)
    sheet.paste(im.resize((tw, th)), (x, y + 22))
    d.text((x + 6, y + 3), lab, fill="#FACC15", font=font)
sheet.save(spec["out"])
print("wrote", spec["out"])
