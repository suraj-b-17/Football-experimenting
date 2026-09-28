"""Loads the DFL/IDSSE Bundesliga open dataset (real TRACAB optical tracking —
every player, every frame, no broadcast visibility gaps) via kloppy, which
fetches it from the dataset's re-host of figshare 10.6084/m9.figshare.28196177
(CC BY 4.0 — see LICENSES.md)."""
import os
import sys

from kloppy import sportec

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kloppy_common import from_kloppy, PITCH_X_M, PITCH_Y_M  # noqa: F401

# 2 top-flight Bundesliga + 5 second-division matches in the open release
MATCH_IDS = ["J03WPY", "J03WMX", "J03WN1", "J03WOH", "J03WOY", "J03WQQ", "J03WR9"]

def load_match(match_id):
    tracking = sportec.load_open_tracking_data(match_id=match_id, coordinates="tracab")
    events = sportec.load_open_event_data(match_id=match_id, coordinates="tracab")
    return from_kloppy(tracking, events)

if __name__ == "__main__":
    g = load_match("J03WPY")
    print(g["players"].shape, g["ball"].shape, g["events"].shape, g["flips"])
