# Benchmarks

Two different things are measured here and they are never mixed:

- **Positional error** — how far the reconstructed position is from where the player really was.
- **Structural realism** — whether the reconstruction *behaves* like real football: the back line
  moving together, attackers near the offside line, players moving with the ball during passes,
  team length and width. A reconstruction can look more realistic while being less accurate, and the
  reverse. Where that happened below it is stated.

Everything is reproducible from `training/` (commands at the bottom). Numbers are copied from the
runs' output files, not hand-adjusted.

## What was wrong with the previous benchmark (11.06 m)

The previous headline, 11.06 m median error, came from `training/benchmark.py` on **one** Metrica
match. Answering the two questions asked:

- **Matched anchor?** Partly. The benchmark built its features the way production did — a whole-match
  median of the player's own sparse touches, and a ball proxy from event start points — so it
  measured the shipped *construction*. But the model it scored was **trained on different features**
  (a ±60 s rolling median of *dense true* positions, and the *true* ball), so the model never saw
  anything like its inputs. The benchmark also used Metrica's own event feed, which is sparser and
  typed differently from StatsBomb's, and scored only on a 5 s grid between each player's first and
  last touch.
- **Matched ball proxy?** In construction yes, in training no (see above).
- **Did it describe the shipped animation?** Only approximately. Scored the fair way — the same
  simulated StatsBomb-like inputs on 10 held-out matches — the old pipeline gets **11.13 m**, so the
  number was about right for tracking-style moments. But on real StatsBomb data, at held-out shots
  where we know every visible player's true position, the old reconstruction is **17.1 m** median
  off (below). The whole-match anchor is worst exactly at the attacking moments people watch.
- The old claim that Full Realism was "the validated reconstruction" hid that it drew only 31–34 % of
  real event locations within 0.5 m at their own timestamp (anchors were snapped to a 1 s grid and
  treated as 1.5 m noisy).

## How the new model is trained and scored (one feature definition everywhere)

- `common/features.py` is the only place features are built — training, LOMO evaluation and
  production all call it. Inputs: the player's own sparse anchors before and after t (a windowed
  median, never a whole-match constant), time to the nearest anchor each side, anchor count, role at
  t (from StatsBomb position labels, including Tactical Shifts), the ball proxy (position and
  velocity), possession, teammates' and opponents' windowed anchor baselines (deepest / median / most
  advanced, opponents' offside line), and a second stage using estimated own and opposing line
  heights.
- `common/ballpath.py` builds the ball proxy from events in both training and production: start to
  end over the real duration for passes, carries and shots, at rest between events.
- **Sparsity-matched training.** Real tracking (Metrica, DFL/IDSSE) is turned into StatsBomb-like
  sparse anchors and on-ball events by `training/sparsity_sim.py`, calibrated to the real 2015/16
  season computed with the pipeline's own `build_tracks` over all 380 matches:

  | gap between a player's anchors (s) | GK real / sim | DEF real / sim | MID real / sim | FWD real / sim |
  |---|---|---|---|---|
  | median | 42.0 / 9.6 | 14.4 / 6.6 | 9.0 / 7.8 | 14.1 / 19.6 |
  | p90 | 257.5 / 251.8 | 153.1 / 130.4 | 123.7 / 118.0 | 135.6 / 144.4 |
  | max | 1780 / 1280 | 1567 / 804 | 1130 / 476 | 1048 / 653 |
  | share < 10 s | 0.26 / 0.50 | 0.47 / 0.54 | 0.51 / 0.52 | 0.47 / 0.45 |
  | share < 60 s | 0.56 / 0.69 | 0.72 / 0.77 | 0.76 / 0.77 | 0.72 / 0.70 |
  | anchors per 90 min | 54.5 / 52.5 | 100.1 / 118.2 | 123.2 / 123.9 | 110.9 / 101.2 |

  Outfield roles match closely on the whole distribution. The medians differ because the real
  distribution is steep right around 10 s. **Goalkeepers are the weakest match**: the simulator gives
  keepers too many short gaps. Full table: `training/sparsity_compare.csv`.

## Positional error — leave-one-match-out, 10 fully tracked matches

Held-out folds: Metrica × 3 + DFL/IDSSE × 7. Every method sees the **same simulated sparse inputs**
for the held-out match; 1,248,842 scored player-seconds. "OLD" is the previously shipped pipeline
reproduced exactly on those inputs, using the shipped model, which was trained *on these matches* —
so if anything this flatters OLD.

| method | median (m) | mean | p90 | folds better than OLD |
|---|---:|---:|---:|---:|
| OLD (shipped before) | 11.13 | 13.38 | 26.58 | — |
| anchors only: windowed median | 19.24 | 22.08 | 42.58 | |
| anchors only: linear interpolation | 16.14 | 19.01 | 38.26 | |
| new, base features | 9.73 | 11.78 | 23.23 | 10 / 10 |
| new, + teammate/opponent coupling | 9.71 | 11.71 | 23.01 | 10 / 10 |
| **new, + second stage (line heights) — shipped** | **9.58** | **11.58** | **22.70** | **10 / 10** |

The shipped model beats base features in 9 of 10 folds. Error by role and time to the nearest real
anchor (median, m):

| role | model | 0–5 s | 5–10 s | 10–20 s | 20–30 s | 30–60 s | 60–120 s | 120 s+ |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| GK | OLD / new | 2.32 / 2.13 | 4.78 / 5.07 | 5.59 / 5.66 | 6.19 / 5.85 | 6.45 / 6.04 | 6.64 / 5.79 | 6.98 / 5.99 |
| DEF | OLD / new | 4.01 / 2.82 | 8.39 / 7.39 | 10.54 / 9.31 | 12.23 / 10.40 | 13.99 / 11.37 | 15.03 / 11.57 | 17.39 / 13.97 |
| MID | OLD / new | 4.31 / 3.25 | 9.23 / 8.42 | 11.45 / 10.63 | 13.23 / 12.14 | 15.09 / 13.34 | 16.48 / 13.94 | 18.78 / 16.31 |
| FWD | OLD / new | 4.74 / 3.66 | 9.46 / 8.27 | 11.15 / 10.00 | 12.62 / 11.14 | 14.43 / 12.64 | 15.63 / 13.36 | 16.88 / 15.13 |

**SkillCorner, re-tested under the new setup:** adding its 20 matches (detected positions only)
did not help. Base features 10.02 vs 10.06 m median without mean smoothing; coupled 10.05 vs 10.02;
second stage 9.95 vs 9.89 — it lost to Metrica + IDSSE alone in 8 of 10 folds. Not shipped. This is
the same conclusion as the previous round, now under sparsity-matched inputs.

**How much of the remaining error is a limit of sparse events.** Players are more than 10 s from
any real StatsBomb anchor **77 % of the time**. There, median error is 11.2 m; within 5 s of an
anchor it is 3.1 m. No model can recover where a player was during a minute in which the event feed
says nothing about him — the largest share of the remaining error is that.

**Mean smoothing** (`training/tune_mu_smoothing.py`): 0 / 1 / 2 / 3 / 5 / 8 s Gaussian smoothing of
the model mean was scored on held-out error. 2 s was best (fold-average median 9.83 m vs 10.17 m
unsmoothed) and removed > 9.5 m/s steps. It costs sharpness: the 99th-percentile one-second step is
4.6 m vs 6.1 m in real tracking.

## Calibration on a held-out source

Uncertainty multipliers are fit on the Metrica folds only, then tested on the 7 DFL/IDSSE folds
(a different league and provider):

| | 50 % band | 68 % band | 90 % band |
|---|---:|---:|---:|
| multiplier (fit on Metrica) | k50 = 0.818 | k68 = 1.110 | k90 = 1.763 |
| actual coverage on DFL/IDSSE | 46.7 % | 64.5 % | 87.7 % |
| previous version, same kind of check | 29.6 % | — | 76.0 % |

The rings are still slightly over-confident on a new source, but by 3–4 points, not 15–20.

## Real StatsBomb check: held-out shot freeze frames

StatsBomb records the position of every visible, named player at each shot. On 80 random matches
(`tests/analysis/freeze_holdout.py`), 40 % of shots were held out. The match was reconstructed
without their freeze frames and compared with the real positions: 825 shots, 10,672 player positions.
Shots are a biased sample — attacking moments around the box — so read this as a check on those
moments, not a season-wide error.

**Verified on the code that actually ships.** An earlier bug in `apply_to_match.py` (mean-smoothing
run on a grid with irregular segment-boundary points — CHANGELOG_fix.md D-mean-smoothing fix) was
found and fixed *after* the first version of this table was generated. Re-run in full on the fixed
code before publishing this table: the numbers below are unchanged within noise (9.37→9.39 m,
10.44→10.45 m), because the bug only affected timestamps right at segment boundaries (substitutions,
cards, kickoffs, period ends), which rarely coincide with a held-out shot's own instant. The
leave-one-match-out benchmark and calibration numbers elsewhere in this document were **never
affected** — `training/train_v2.py::reconstruct` builds its own grid directly from each player's
regular per-second rows and never inserts the irregular boundary points that caused the bug, so
nothing there needed re-running.

| role | new (other shots' freeze frames used) | new, no freeze frames at all | OLD shipped |
|---|---:|---:|---:|
| GK | 0.28 | 0.27 | 0.99 |
| DEF | 8.52 | 9.54 | 17.92 |
| MID | 12.45 | 13.50 | 20.57 |
| FWD | 10.84 | 11.75 | 16.73 |
| **all** | **9.39** | **10.45** | **17.08** |

Keepers are near-exact because they have their own event at every shot. Freeze frames are used as
anchors in production (−1.06 m at held-out shots).

**How much of the 17.1→9.4 m gap is the anchor-bug fix alone, vs the new model?**
(`tests/analysis/shot_decomposition.py`, same 80 matches, same held-out shots, four reconstructions
compared): **A** the old pipeline exactly as shipped (whole-match-median anchor) — 17.08 m. **B** the
old pipeline with *only* the anchor definition fixed (windowed median instead of whole-match) —
everything else (old model, old event-based ball proxy, old grid, old Kalman) unchanged — 15.97 m.
**C** the new pipeline, no freeze frames — 10.45 m. **D** the new pipeline shipped, with freeze
frames — 9.39 m. So: fixing *only* the anchor bug recovers 1.11 m of the 7.69 m total gap (14 %); the
other 6.58 m (86 %) comes from the new model (sparsity-matched training, shared features, coupling,
the second stage) and freeze frames together. The anchor bug was real and worth fixing, but it was a
minority of the improvement — most of it is the retrained model.

## Structural realism — real tracking vs reconstruction (held-out folds)

Computed on the same frames for real and reconstructed positions (both teams ≥ 9 outfield players
tracked). "Back line" = the 4 deepest outfield players.

| statistic | REAL | OLD | new (shipped) |
|---|---:|---:|---:|
| team length, median (m) | 33.2 | 32.4 | 28.4 |
| team width, median (m) | 38.8 | 43.5 | 33.0 |
| back-line height vs ball x, slope | 0.47 | 0.37 | 0.50 |
| back-line depth spread, median (m) — "moves together" | 8.8 | 10.6 | 7.4 |
| back-line width spread, median (m) | 28.9 | 31.6 | 24.9 |
| keeper x, median (m from own goal) | 12.9 | 11.8 | 14.3 |
| share of in-possession frames with an attacker beyond the last defender | 24.1 % | 28.7 % | 24.4 % |
| outfield movement during long balls (≥ 30 m, ≥ 2.5 s), median over 874 balls (m) | 9.0 | 10.9 | 8.7 |
| peak simultaneous team speed (mean of outfield players, max over the match, m/s) | 7.4 | 6.7 | 6.4 |
| 99th-percentile team speed (m/s) | 4.8 | 5.6 | 4.1 |

Better than before and close to real: the back line follows the ball (slope), attackers sit on the
offside line at the real rate, and movement during long balls matches real tracking. Before, every
player drifted 20+ m during a long ball; now it's 8.7 m vs 9.0 real.

Worse than real, stated plainly: **teams are too compact** (width 33 vs 39 m, length 28 vs 33 m). This
is regression to the mean — when the event feed says nothing about a player, the error-minimising
guess pulls him toward the middle. The back line is tighter than real (7.4 vs 8.8 m), and team speed
peaks are slightly lower than real.

**Tested, not shipped as "accuracy": a shape stretch** (`training/shape_correction.py`). Scaling each
team's outfield shape about its centroid by 1.2 (faded to zero near real anchors) makes length,
width and back-line spread match real (33.3 / 38.6 / 29.2 m). But held-out error gets **worse**
(fold-average median 9.68 → 9.91 m). It is available in the viewer only as an off-by-default,
labelled display option ("Spacing: real-scaled (display only)").

**T3 features, kept only if held-out error doesn't get worse:** teammate/opponent coupling
(9.71 vs 9.73 m, shape length 29.4 vs 31.1 m — slightly *more* compact) and the second stage (9.58 m,
attacker-beyond-line share 24.4 % vs real 24.1 %, previously 28.8 % with base features) both
improved held-out error, so both are kept. The coupling features alone did not bring the shape closer
to real.

**On real StatsBomb matches (no tracking needed)** — offside consistency
(`tests/analysis/offside_consistency.py`): at every pass with a named recipient, is the recipient
drawn offside? On 60 matches: recipients of passes StatsBomb flagged **Pass Offside** are drawn
offside 75.0 % of the time (previous version 69.3 %); recipients of completed passes are wrongly drawn
offside 2.6 % of the time (previous 2.9 %). A real improvement, but a modest one.

## Reproducing

```
python training/tracking_prep.py          # 30 tracking matches -> 5 Hz arrays (cached)
python training/real_sparsity_stats.py    # real StatsBomb gap distributions, 380 matches
python training/calibrate_sparsity.py     # simulator parameters
python training/build_rows_v2.py          # sparsity-matched rows
python training/calibrate_kickoff.py      # kickoff templates from real kickoff frames
python training/train_v2.py --configs B C --featsets base coupled --stage2   # LOMO
python training/tune_mu_smoothing.py --name stage2_B
python training/ship_v2.py --featset stage2 --config B
python tests/analysis/freeze_holdout.py 80
```
