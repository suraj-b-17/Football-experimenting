# Premier League 2015/16 — match replays

**To watch: open `output/index.html`** (double-click it) and click a match. No server, no command
line and no internet connection are needed. The `output/` folder can be copied or moved anywhere;
all paths inside it are relative.

If a browser shows the match page with a **blank pitch**, it is blocking scripts on local files: its
developer console (F12) will show an error about loading `data/<id>.js`. Chrome, Edge and Firefox
work as-is (tested, see below). Only in that case, serve the `output/` folder with any local web
server as a fallback.

A 2D replay of every match of the StatsBomb open-data 2015/16 Premier League season (380 matches),
built only from publicly available, clearly licensed data. Two kinds of data, kept separate:

- **What the replays are made from:** StatsBomb's open event data. StatsBomb records where a named
  player was only when he did something — touched the ball, pressed, was fouled, appeared in a shot
  freeze frame. **A real anchor's position is never invented or moved** — both display modes hit
  every kept anchor within 0.5 m at its own timestamp, checked automatically for all 380 matches
  (0 misses). Its *time* can be adjusted in two narrow, logged cases: (1) two of a player's own
  anchors land within ~0.05 s of each other or imply an impossible speed (StatsBomb tags some
  near-simultaneous actions this way) — the later one is re-timed to the earliest physically possible
  moment, or, if it is a true duplicate of the same instant, merged; (2) roughly 1 event in 3,400 has
  a corrupted source timestamp (a real StatsBomb data quirk, not a pipeline artifact — see
  `LICENSES.md`/`CHANGELOG_fix.md`) and is re-timed from its own recorded pass duration or from where
  it sits in the real event stream. Season-wide: 66% of raw anchor observations are kept unchanged,
  34% are exact-position duplicates correctly merged into one, 3.3% are re-timed (median shift 0.1s),
  and 0.04% are dropped as genuinely unrecoverable — never guessed. Everything between anchors is a
  statistical reconstruction, with its uncertainty shown in Full Realism mode.
- **What teaches the reconstruction how players move:** real player-tracking data — Metrica Sports
  (3 matches) and DFL/IDSSE Bundesliga (7 matches). SkillCorner's 20 open broadcast-tracking matches
  were tested and did not improve held-out accuracy, so the shipped model does not use them.

**Read `LICENSES.md` before publishing anything.** StatsBomb's terms prohibit commercial use and
redistribution of the raw data, and require the StatsBomb logo on published analysis.

## How accurate is it — and how realistic does it look?

Measured separately (details and every table in `BENCHMARKS.md`):

- **Positional error**, held out, on real tracking matches fed StatsBomb-like sparse inputs: median
  **9.6 m** (previous version: 11.1 m, measured the same way). On real StatsBomb shots, against the
  true positions of every visible player: median **9.4 m** (previous version: 17.1 m).
- **Most of what remains is a limit of the event feed:** a player is more than 10 s from any real
  event 77 % of the time. Within 5 s of one, median error is about 3 m; beyond 10 s it is about 11 m.
- **Structure:** the back line follows the ball like real teams, attackers sit on the offside line at
  the real rate, and players move with the ball during long balls as much as real players do. Still
  wrong: teams are **more compact than real** (about 33 m wide vs 39 m). An optional display-only
  "real spacing" stretch fixes the look but makes measured accuracy slightly worse, so it is off by
  default and labelled.
- **How much better it looks vs how much more accurate it is:** the look improved far more than the
  accuracy. Players no longer freeze (near-static minutes fell from 11–17 % to ~0 %), no longer all
  sprint during long balls, carries move at their real speed with the ball on the carrier, and
  nobody teleports. The measured accuracy gain is real but modest: about 14 % on tracking and about
  45 % at real StatsBomb shots.

## Playback pacing — replays no longer run at exactly match speed

Since the polish round (`CHANGELOG_fix.md`, "Polish round"), **1x playback is not a strict 1:1 copy
of the match clock**, in both display modes:

- Whenever something on screen would move faster than real players or balls do (sprinting over
  8.0 m/s, carrying over 7.5 m/s, the ball over 30 m/s — caps measured on the real tracking data),
  playback slows down locally instead of drawing a "super dash". Every real event still happens at
  its own match-clock time and place; the clock readout is exact at every real event and moves at a
  varying rate in between.
- Sustained idle play may run up to 1.5x, and the dead time of a stoppage (throw-in, corner, goal
  kick, foul, offside, goal, penalty) is skipped in 1.6 s under a dimmed "skipping" overlay, after
  the ball going out / the foul has been shown and with a team-coloured restart banner.
- The speed buttons (0.5x–10x) multiply on top. A whole match at 1x now takes roughly 60–70 % of
  its real duration (BENCHMARKS.md, "Playback pacing").

## What changed in this round

See `CHANGELOG_fix.md` for every fix, the decision log and the before/after evidence. The polish
round (pacing, the drawn ball, stoppages, crowded duels, pass height) is its last section. Before that:
one shared feature definition for training, evaluation and production (the old model was trained on
features it never saw in production); training inputs simulated to match StatsBomb's real sparsity;
the ball proxy built the same way in training and production; red cards and second yellows on any
event now end a player's track; Player Off / Player On handled; real carry, pass and shot durations
used; kickoff shapes learned from real tracking; Tactical Shifts used; shot freeze frames used as
anchors; carries drawn on their real path with the ball on the carrier; per-match automated QA
(`output/qa_report.csv`).

## Layout

- `common/` — shared by training and production: `features.py` (the one feature definition),
  `ballpath.py` (ball proxy from events), `smoother.py` (anchor-exact Kalman/OU smoother).
- `training/` — tracking loaders, `tracking_prep.py`, `sparsity_sim.py` (StatsBomb-like sparse
  inputs from real tracking), `build_rows_v2.py`, `train_v2.py` (leave-one-match-out evaluation),
  `ship_v2.py`, calibration scripts. Old-round scripts (`pool_train.py`, `benchmark.py`, …) are kept
  for reference; they describe the previous model.
- `pipeline/` — StatsBomb loader, `build_match.py` (anchors, intervals, roles, kickoffs),
  `apply_to_match.py`, `build_payload.py`, `run_season.py`, `qa.py`.
- `viewer/` — `viewer.html` + `app.js`, one generic viewer for every match. Opens in **Tactical
  Clarity** (less wobble, receivers run onto passes); **Full Realism** shows every knot of the same
  reconstruction plus uncertainty rings. Pacing, stoppage skips, the ball model, the "on the ball"
  halo and lofted-pass drawing are the same in both.
- `tests/` — the Node harness that runs the real `app.js` headlessly, drivers, analyses, and the
  before (`tests/baseline/`) / after (`tests/evidence/`) evidence.
- `models/`, `output/` — generated.

## Reproducing

```
pip install pandas numpy scipy scikit-learn joblib kloppy
# training (see BENCHMARKS.md for the full sequence)
python training/tracking_prep.py && python training/build_rows_v2.py
python training/train_v2.py --configs B --featsets base coupled --stage2
python training/ship_v2.py --featset stage2 --config B
# application
python pipeline/run_season.py            # all 380 matches -> output/
python pipeline/qa.py                    # -> output/qa_report.csv
```

Downloaded data is cached in `~/.cache/football_anim/` (override with `FOOTBALL_ANIM_CACHE`),
outside this repo and never committed, per `LICENSES.md`.

Each match is `output/data/<match_id>.js`, a plain script that the viewer loads with a `<script>`
tag (browsers allow that on `file://`, but block `fetch()` of a `.json`). The stylesheet is compiled
locally into `viewer/viewer.css` — no CDN or web fonts. To rebuild it after changing classes:
`npx tailwindcss@3 -c viewer/tailwind.config.js -i viewer/tailwind.src.css -o viewer/viewer.css --minify`
(build time only). To regenerate `index.html` and copy the viewer without rebuilding matches:
`python pipeline/run_season.py --site-only`.

Browser test (headless Chrome offline and Firefox, `file://` URLs, 5 matches, fails on any console
error or non-local request): `NODE_PATH=<folder with puppeteer-core> node tests/browser/file_test.js`.
Screenshots go to `output/qa_screens/`.
