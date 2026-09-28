"""Where downloaded third-party data lives. Deliberately OUTSIDE the project
folder (and outside OneDrive): nothing from StatsBomb, SkillCorner, Metrica or
DFL/IDSSE is ever stored in, or redistributed with, this project — every
source is fetched from its own official location on first use. See
LICENSES.md."""
import os

CACHE_DIR = os.environ.get(
    "FOOTBALL_ANIM_CACHE",
    os.path.join(os.path.expanduser("~"), ".cache", "football_anim"),
)
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(PROJECT_DIR, "models")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")

def cache(*parts):
    p = os.path.join(CACHE_DIR, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p
