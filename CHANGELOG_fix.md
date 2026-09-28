# Fix changelog and decision log

Follow-up to `REPORT_diagnosis.md`. Each entry: what changed, and for decisions that
could not be deferred, the reasoning. Numbers are quoted from the runs that produced them.

## T0 — reproduction (before any change)

Harness: `tests/viewer_harness.js` evals the real `viewer/app.js` in Node; drivers in
`tests/drivers/`. Raw outputs in `tests/baseline/`. Source snapshot of the pre-change code:
`tests/baseline/src_before/` (the project is not a git repository, so this copy is the
"before" reference).

- **Default mode**: the viewer opens in **Full Realism** (`displayMode = 'realism'`), "Match Ends"
  orientation.
- **01:33 frame, which mode matches the screenshot**: **Tactical Clarity**. Tactical puts Giroud at
  shared (68.3, 37.3) and Mignolet at shared x=100.2; Full Realism puts Giroud at (54.0, 38.1) and
  Mignolet at 93.8. The screenshot (Giroud ≈ (68,37), Mignolet ≈ x=100) is Tactical.
- **Five passes, Tactical mode** (20 outfield players each; "<1 m" = count of players whose path
  length is under 1 m in that phase):

  | pass | flight median / max (m) | flight <1 m | post-2 s median / max (m) | post-2 s <1 m |
  |---|---|---:|---|---:|
  | SHORT | 0.72 / 2.93 | 12 | 1.57 / 7.56 | 6 |
  | LONG | 0.46 / 4.53 | 14 | 0.28 / 3.43 | 15 |
  | THROUGH | 0.47 / 8.25 | 14 | 0.46 / 9.09 | 15 |
  | CROSS | 0.32 / 1.12 | 18 | 0.46 / 1.50 | 16 |
  | LONG_GAP | 0.63 / 4.03 | 14 | 0.55 / 2.98 | 16 |

  Full Realism, same passes: LONG flight median 23.66 m (every player 17.9–28.3 m), 0 players under 1 m.
  So the two modes fail in opposite directions: Tactical freezes, Full Realism sprints everyone.
- **Near-static 60 s windows (<3 m), Tactical mode, 4 matches pooled**: GK 48.5 %, DEF 12.2 %,
  MID 7.6 %, FWD 10.4 % (per match 11.4 %, 11.8 %, 13.9 %, 17.5 %). Of these, 383 of 11,856
  windows had ≥2 anchors (i.e. were static despite plenty of data). Full Realism: 0.0–1.5 %,
  reproducing the report's numbers exactly.

## T5b — carry "teleporting": diagnosis (before any change)

Measured at 60 fps on the shipped viewer (`tests/baseline/app_before.js`) and payloads, matches
3754129, 3754348, 3754258. Drivers: `tests/drivers/carry_diag.js`, `overspeed_causes.js`.
Raw: `tests/baseline/carry_diag_*.json`, `overspeed_*.json`, `carry_detail_before.json`.

**Per carry** (849 / 692 / 823 carries):

| match | mode | real speed median | displayed mean speed median | max instantaneous (median / max) | carries with a frame > 9.5 m/s | frames > 9.5 in carries |
|---|---|---:|---:|---|---:|---:|
| 3754129 | Full Realism | 2.52 | 2.26 | 5.06 / 22.0 | 29 | 750 |
| 3754129 | Tactical | 2.52 | 2.52 | 4.48 / 88.4 | 86 | 1832 |
| 3754348 | Full Realism | 2.57 | 2.19 | 5.08 / 11.9 | 29 | 828 |
| 3754348 | Tactical | 2.57 | 2.57 | 4.27 / 94.1 | 99 | 1926 |
| 3754258 | Full Realism | 2.50 | 2.18 | 5.37 / 10.9 | 19 | 308 |
| 3754258 | Tactical | 2.50 | 2.51 | 4.28 / 1064.4 | 106 | 1903 |

Whole-match frames above 9.5 m/s (all players): Realism 4826 / 4698 / 3896, Tactical 3329 / 3645 / 4004.

**Largest single-frame jumps**: Tactical — Jesús Navas 17.74 m in one frame (3754258, t=2772.58);
Fernando 2.93 m (two anchors 0.04 s apart, 4.4 m apart); Joe Hart 1.85 m/frame (a real carry of
13.2 m in 0.19 s = 68.9 m/s); Giroud 1.92 m/frame at t=0–0.3 s. Full Realism — nothing above
0.37 m/frame (22 m/s, Monreal, between two anchors 35 m and 1.7 s apart).

**Drawn ball**: source is `drawEventAction(currentEvent, progress)` — the current event's start
location, eased toward `endX/endY` over the whole interval until the next event. Carries have no
`endX/endY` in the payload, so **during a carry the ball sits at the carry's start** and jumps at
the next event. Ball–carrier gap during carries: median of per-carry means 1.7–2.1 m, max 56.7–65.9 m;
for carries longer than 10 m the maximum gap is 0.93–0.94 × the carry length (i.e. the ball
never moves). For passes the ball also eases over the whole gap to the next event, not over the
pass's real duration.

**Waypoint lists** (shipped payloads): 750 / 612 / 773 consecutive waypoints with **equal
timestamps**, all at 0.00 m distance (the old dedupe mixed 3- and 4-tuples so `dict.fromkeys`
missed them; Ball Receipt and Carry start share a timestamp). They are not jumps, but they zero
the PCHIP tangent at every carry start (stop-start). 69 / 87 / 77 consecutive waypoint pairs are
<0.5 s apart and >1 m apart; the top sources are Carry-start → Pass (22 / 19 / 29), i.e. carries
shorter than 0.5 s — carries whose real implied speed exceeds 9.5 m/s: 19 / 32 / 33 per match.
None start at a carry *end* waypoint (the carry end shares its timestamp with the following
Pass and was merged into it — same place). Times are never decreasing.

**Carry start vs preceding Ball Receipt\***: identical location in the median case; >1 m apart in
27 / 20 / 26 carries, >3 m in 20 / 15 / 20, max 24.3 m. Those are real StatsBomb disagreements,
shown as a very fast move between two real points 0 s apart (the receipt and carry start share t).

**Cause attribution** (every 60 fps frame > 9.5 m/s, `overspeed_causes.js`, 3754129 shown; the
other two matches have the same ordering):

| Tactical Clarity | frames | Full Realism | frames |
|---|---:|---|---:|
| spline overshoot during carries whose real speed ≤ 9.5 | 1246 | spline overshoot between 1 Hz grid points (no anchor involvement) | 4191 |
| reception ease (receiver's run peaks at 3× mean speed with easeInOutCubic) | 863 | spline overshoot during carries | 366 |
| other spline overshoot | 504 | carries whose real speed > 9.5 (data) | 209 |
| carries whose real speed > 9.5 (data) | 323 | kickoff seed | 60 |
| kickoff seed (template at t=0, e.g. Giroud at x=89 m then centre spot 0.51 s later) | 191 | | |
| anchor segments implying > 9.5 m/s / two anchors < 0.5 s apart (data) | 184 | | |
| reception window ends → snap (few frames, but the biggest single jumps, up to 17.7 m) | 18 | | |

**Verdict** — which causes the teleporting, from the evidence, not guessed:
1. **Tactical Clarity** (the mode the user was watching): (a) the reception window runs from the
   pass to the receiver's *next own touch*, not the ball's arrival — up to 26 s for Navas — and the
   display snaps from the pass end to the real track when it closes (the 17.7 m one-frame jump);
   (b) PCHIP/Hermite interpolation between touches overshoots speed on short carries, made worse
   by zero tangents at duplicated timestamps; (c) the t=0 kickoff template.
2. **The drawn ball ignores carries entirely** (stays at the start; for long carries the gap
   reaches the full carry length). This alone makes every carry look like the player runs away
   from the ball and then the ball teleports to him.
3. **Full Realism**: not teleporting in the frame-jump sense (max 0.37 m/frame), but carries are
   smoothed away (displayed mean speed 2.2 vs real 2.5 m/s) and **real anchors are not hit**:
   66–69 % of anchors are displayed more than 0.5 m from their real location at their own
   timestamp (median 0.75 m, max 18.4 m) — anchors are snapped to the nearest 1 s grid point
   (median offset 0.15–0.31 s) and observed with 1.5 m noise.
4. **Data anomalies** (real StatsBomb): 19–33 carries per match imply > 9.5 m/s; ~70–90 consecutive
   events per match < 0.5 s and > 1 m apart; 20–26 carries start > 1 m from their own receipt.
   Timestamp collisions are real but harmless in distance (0.00 m).

## Decisions

- **D1 — T0 runs on the already-built payloads.** The "before" state is the 380 payloads in
  `output/data/` plus `viewer/app.js` as they were at the end of the previous round. Nothing in
  `output/` is rebuilt until the T0 measurements are captured, so "before" numbers describe what
  actually shipped.
- **D2 — Possession = team of the most recent on-ball event, in training and production.** The old
  pipeline used StatsBomb's `possession_team` label in production but tracking-provider possession in
  training. A tracking match has no StatsBomb possession chains, so the only definition computable
  identically on both sides is "team of the last on-ball event"; matching beats the richer label.
- **D3 — Sparsity simulator calibrated on the whole CDF, not the median.** Real StatsBomb gaps are
  bimodal (about half under 10 s, then a long tail), so the median sits on a steep slope and swings
  with tiny shape changes. The simulator matches the real CDF within ~0.1 at 1, 2, 5, 10, 30, 60, 120,
  300 s for every role, p90 within 3–14 % and anchors/90 within 18 %; medians still differ
  (DEF 6.6 vs 14.4 s, GK 9.6 vs 42 s). Side-by-side table in `training/sparsity_compare.csv`.
- **D4 — Tactical Shift is no longer ignored.** Tested on 625 shifts / 4,157 player-shift pairs
  (`tests/analysis/tactical_shift_check.py`): players whose position label changes move their
  own-anchor centre by a median 21.1 m (10 min before vs after) vs 15.4 m for unchanged labels. Role
  at time t now comes from the latest position label (Starting XI, Tactical Shift, or the player's
  own events), and the windowed anchor baseline follows the move.
- **D5 — Model mean smoothed with a 2 s Gaussian.** Without the old hand-set 6.5 m/s limiter the
  model mean reacts instantly to possession and ball changes (2.3 % of 1-s steps > 9.5 m/s).
  Candidates 0/1/2/3/5/8 s were scored by LOMO held-out error (`tune_mu_smoothing.py`): 2 s was
  best (median 9.83 m vs 10.17 m unsmoothed) and removes > 9.5 m/s steps (real tracking: 0.01 %).
  Cost, stated plainly: the 99th-percentile 1-s step is 4.6 m vs 6.1 m in real tracking — the
  reconstruction moves slightly less sharply than real players.
- **D6 — Carries drawn exactly on the real path, no blend.** The reconstruction passes through the
  carry's start and end anchors at t0 and t1 (exact knots), so the real carry path is already
  position-continuous with it at both edges. A smooth blend weight was tried first and measurably
  pushed 5 fast real carries (7.5–9 m/s mean) to 9.75–12.4 m/s displayed, so it was removed. Speed
  profile: trapezoidal ease (accelerate / cruise / decelerate), ramp fraction chosen so the peak is
  ≤ 9.5 m/s; carries whose real implied speed is > 9.5 m/s are logged in the payload's `anomalies`
  and drawn at 9.5 m/s, arriving late, then blending back over 0.5 s.
- **D7 — Anchor timestamps rounded once, at creation.** A 0.001 s disagreement between NumPy and
  Python rounding of the same float dropped one knot from Tactical's thinned curve (a 1.25 m anchor
  miss). Anchors are now rounded in `build_match` and the viewer matches knots with a 2 ms tolerance.
- **D9 — Timestamp collisions resolved without moving any location.** The first T1 rewrite merged a
  player's anchors closer than 0.05 s by *averaging* them — which invents a location (e.g. a Ball
  Receipt at (25.5, 43.0) and a Pass at (36.1, 44.3) by the same player at t=548.036 became one
  anchor at (30.8, 43.7)). Found by the related-events count, fixed before any payload shipped.
  Rule now (`build_match.resolve_collisions`), in StatsBomb stream order: ≤ 0.5 m apart → the later
  is a duplicate and dropped, the first location kept as recorded; farther apart → the later anchor
  keeps its real location and is re-timed to the earliest physically possible moment
  (distance / 9.5 m/s after the first) if that is still before the player's next anchor, otherwise
  dropped. Every re-timed / dropped case is written to the payload's `anomalies` and counted in
  `qa_report.csv`. Match 3754129: 69 re-timed (median 1.0 m, max 10.7 m), 9 dropped.
- **D8 — Half-time is a cut.** Segments break at period boundaries; the viewer shows the second-half
  kickoff shape from the first frame of period 2. QA skips the boundary frame (it is a scene change,
  not movement).

## What changed (by task)

- **T1a Cards.** Dismissal cards are read from *any* event sub-object with a `card` field (today
  `foul_committed` and `bad_behaviour`). Red Card and Second Yellow end the player's on-pitch
  interval. Before: Coutinho's second yellow on a `Foul Committed` (3753984, t=3335.1 s) left him on
  the pitch for 46 minutes. Checked season-wide by `qa.js` (active player count vs cards,
  substitutions and Player Off/On, every 10 s, every match).
- **T1b Player Off / Player On.** Tracks have a list of on-pitch intervals; Player Off closes one,
  Player On reopens it; the viewer hides the player in between (e.g. Coquelin 873.4–892.3 s in
  3754129; Bony in 3754258). Injury Stoppage is a stoppage, not an exit (the player stays on).
- **T1c `duration`** captured for every event; pass arrival = t + duration (was: the receiver's next
  own touch, up to 26 s later); carries/passes/shots use their real duration in the ball path.
- **T1d Kickoffs.** Kickoffs detected from StatsBomb (`From Kick Off` possessions: period starts and
  restarts after goals). Every player without his own event at that moment gets a soft anchor from
  templates learned on 27 real kickoffs / 591 player positions in the tracking data
  (`models/kickoff_template.json`), clamped to his own half and, for the receiving team, outside the
  centre circle; x/y uncertainty = the template's residual SD (depth 0.5–7 m, lateral 7–11 m).
  Replaces the hand-set t=0 formation seed (e.g. Giroud drawn at x=89 m, then at the centre spot
  0.5 s later). Tactical Shift: see D4.
- **T1e Half-time.** Measured before: no spline spike at the boundary (0.0 s clock gap, max 7.3 m/s),
  but the smoother ran straight through the 15-minute break so the second-half kickoff shape was
  never enforced. Now: segments break at period boundaries (D8) and period starts get kickoff anchors.
- **T1f Dead code.** Removed: `passHeight` (never read), the Full-Realism "wander" jitter (never ran
  for real players), the averaging anchor merge (D9). `freezeFrame` is no longer dead — it is used (T4).
- **T2** one feature function, sparsity-matched training, event-built ball proxy in training,
  retrained, LOMO — results in `BENCHMARKS.md`. Shipped: stage-2 model on Metrica + DFL/IDSSE
  (`models/v2_bundle.joblib`). SkillCorner re-tested: no gain, not shipped.
- **T3** coupling + second stage kept (both improve held-out error); shape stretch rejected as
  accuracy, offered as a labelled display-only toggle. Post-constraint on forwards not added: the
  shipped model already matches the real share of attackers beyond the last defender (24.4 % vs
  24.1 %), so there was nothing measured to correct.
- **T4 StatsBomb fields** (season counts from `tests/analysis/field_counts.py`, 380 matches):

  | field | count | status | evidence |
  |---|---:|---|---|
  | shot freeze frames | 9,835 shots, 126,489 named positions, 0 without player id | **used** (anchors) | held-out shots: 10.44 → 9.37 m |
  | per-event position label, Starting XI, Tactical Shift, Substitution | every event | **used** (role at t) | D4: label changes ↔ 21.1 vs 15.4 m moves |
  | off_camera | 18,529 events | **used** (anchor uncertainty 1.5 m instead of 0.05 m) | not testable against truth (no tracking for StatsBomb matches) — a design choice, not a measured gain |
  | Pass Offside outcome | 1,381 | **used as a check** (offside consistency), not as a model input | 69.3 % → 75.0 % drawn offside |
  | under_pressure | 279,690 | not applicable | describes the ball carrier's event and gives no location for the presser; the presser's own Pressure event is already an anchor |
  | counterpress | 42,324 | not applicable | a flag on Pressure events that are already anchors |
  | related_events | 1.93 M references | not applicable: **0 new locations** | every related event is itself in the stream (already an anchor); the only mismatches are the collisions of D9 |
  | pass flags through_ball / cross / switch / cut_back | 1,621 / 9,545 / 10,488 / 788 | not applicable to the learned model | no equivalent can be simulated on tracking, so a feature would be unmatched between training and production — the exact problem this round removed |
- **T5 Movement during passes.** Ball path = pass start → end over the real duration (all outcomes),
  carries likewise, shots to shot end, at rest between events; the same function feeds the model.
  Receiver anticipation (Tactical) runs from the pass to the ball's real arrival with a speed-capped
  profile. Team displacement during long balls calibrated against 874 real long balls: 8.7 m vs
  9.0 m real (old 10.9 m). Peak simultaneous team speed 6.4 m/s (real 7.4, old 6.7).
- **T5b Carries.** Fixed as diagnosed above: carry drawn on its real start→end path over its real
  duration in both modes (D6), ball on the carrier (0.7 m offset; the model's ball path otherwise),
  anchors at exact times, collisions resolved without moving locations (D9), carries implying
  > 9.5 m/s logged in `anomalies` and drawn at 9.5 m/s.
- **T6 Viewer.** Tactical Clarity is built on the same smoothed reconstruction as Full Realism and
  differs only by: no rings, thinned non-anchor knots (every anchor kept; knots kept wherever
  thinning would add speed), receiver anticipation, stoppage compression with labels. **Default:
  Tactical Clarity** — both modes hit every non-anomalous anchor (QA); Tactical has less wobble and
  the pass runs, and is the better-looking one by the measures below.
- **T6b Opens by double-click.** Cause confirmed in headless Chrome from `file://`: `Access to fetch
  at 'file:///…/data/3754129.json' from origin 'null' has been blocked by CORS policy` →
  `Uncaught (in promise) TypeError: Failed to fetch` (blank pitch). `index.html` also depended on the
  Tailwind CDN and Google Fonts (unstyled offline). Fixed: payloads are `output/data/<id>.js`
  setting `window.MATCH_DATA_<id>`, loaded by an injected `<script>`; no fetch/XHR/module/worker/wasm
  anywhere; stylesheet compiled locally (`viewer.css`), system fonts; `index.html` embeds the match
  list with relative `viewer.html?match=<id>#match=<id>` links (the query string survived on
  `file://` in both Chrome and Firefox; the viewer reads either). Payload size 1,543 MB → 1,281 MB
  (reconstruction knots and ball path rounded to 0.1 m; largest position change 0.0500 m, checked
  for every value; anchors and event locations keep 0.01 m). Tested for real
  (`tests/browser/file_test.js`): Chrome headless **offline** and Firefox headless, `file://`, 5
  matches each (3754129, 3754348, 3753984 red card, 3754258, 3754078), click-through from
  `index.html`, pitch and both teams rendered (pixel counts), 10 s playback (clock +10.0 s), timeline
  scrub exact, both modes switch, screenshots at 01:33 and 60:00 in `output/qa_screens/<browser>/` —
  **5/5 pass in both, 0 console errors, 0 failed or non-local requests**. Same test on a copy of the
  site in another folder (path with spaces): 5/5. Only browser difference: a few hundred pixels of
  anti-aliasing. The QA now fails if any payload, the viewer or `index.html` contains
  fetch/XHR/module/worker/wasm or an external load (self-tested on 8 violation types).

## Evidence — before / after

Before = shipped at the start of this round (`tests/baseline/`), after = this round
(`tests/evidence/`, built by the same code as `output/`).

**01:33 frame, Arsenal v Liverpool (t=93.743 s)** — own frame (x = m from own goal); offside line =
second-last opponent; (+/−) = attacker beyond (+) / behind (−) that line:

| mode | team | deepest outfield x | team length | team width | two most advanced vs offside line |
|---|---|---|---|---|---|
| Full Realism before | Arsenal | 12.7 | 41.2 | 40.0 | Giroud 54.0 (−1.6), Özil 48.6 (−7.0) |
| Full Realism after | Arsenal | 10.4 | 26.3 | 25.0 | Giroud 36.8 (−6.9), Sánchez 35.9 (−7.7) |
| Full Realism before | Liverpool | 49.5 | 42.8 | 42.0 | Can 92.2 (0.0), Benteke 90.8 (−1.5) |
| Full Realism after | Liverpool | 61.3 | 31.1 | 36.8 | Benteke 92.4 (−2.2), Can 91.8 (−2.8) |
| Tactical before | Arsenal | 10.1 | 58.2 | 57.5 | Giroud 68.3 (−12.6), Özil 65.8 (−15.1) |
| Tactical after | Arsenal | 10.5 | 26.5 | 25.1 | Giroud 37.0 (−6.8), Sánchez 36.3 (−7.5) |
| Tactical before | Liverpool | **24.1** | **68.6** | 56.7 | Benteke 92.6 (−2.3), Can 91.8 (−3.1) |
| Tactical after | Liverpool | 61.2 | 31.0 | 36.8 | Benteke 92.2 (−2.3), Can 91.8 (−2.7) |

Liverpool are attacking in Arsenal's box here (shot at 01:35). After, they hold a high line and
Arsenal defend compactly in their box. All 22 positions per mode: `tests/evidence/frame_3754129_after.json`
(before: `tests/baseline/frame_3754129_before.json`). One position is a guess worth flagging:
Mignolet 24.7 m off his line, 94 s after and 128 s before his nearest real events.

**Two more frames (t = 100 s)**: Norwich–Liverpool (3754348) Tactical — Liverpool deepest outfield
10.1 → 26.9 m, length 54.7 → 24.6 m; Norwich length 49.4 → 20.3 m. Aston Villa–Man City (3754258)
Tactical — City length 73.2 → 34.4 m, Villa 56.7 → 34.0 m; City's two most advanced were 9.4 m and
1.2 m *beyond* the line before, 0.5 m beyond / 0.4 m behind after.

**Five passes (Arsenal v Liverpool), outfield movement median / max (m), players < 1 m in brackets:**

| pass | Tactical before: flight | Tactical after: flight | Realism before: flight | Realism after: flight | after, 2 s post-reception median (T / R) |
|---|---|---|---|---|---|
| SHORT (1.09 s) | 0.72 / 2.93 (12) | 1.54 / 3.19 (4) | 2.94 / 7.26 (1) | 1.36 / 2.91 (4) | 2.21 / 2.66 |
| LONG (5.39 s) | 0.46 / 4.53 (14) | 17.90 / 24.48 (0) | 23.66 / 28.27 (0) | 17.90 / 24.31 (0) | 2.22 / 2.83 |
| THROUGH (1.92 s) | 0.47 / 8.25 (14) | 5.22 / 11.16 (0) | 6.64 / 9.76 (0) | 5.03 / 10.36 (0) | 3.13 / 3.78 |
| CROSS (1.52 s) | 0.32 / 1.12 (18) | 1.76 / 3.11 (3) | 8.98 / 11.21 (0) | 1.71 / 3.07 (1) | 1.84 / 2.07 |
| LONG_GAP (2.29 s) | 0.63 / 4.03 (14) | 4.07 / 6.47 (0) | 6.59 / 10.15 (0) | 4.24 / 7.21 (0) | 1.75 / 1.89 |

The LONG pass (a 78 m keeper's kick) still moves the team a median 17.9 m of path in 5.4 s (3.3 m/s).
Across 874 real long balls in held-out tracking the reconstruction matches real displacement
(8.7 vs 9.0 m), so this is one long kick rather than a systematic overshoot — but it is on the high side.

**Near-static 60 s windows (< 3 m), 4 matches pooled:**

| | GK | DEF | MID | FWD |
|---|---:|---:|---:|---:|
| Tactical before | 48.5 % | 12.2 % | 7.6 % | 10.4 % |
| Tactical after | 0.1 % | 0.0 % | 0.0 % | 0.0 % |
| Realism before | 1.6 % | 0.6 % | 0.5 % | 0.4 % |
| Realism after | 0.1 % | 0.0 % | 0.0 % | 0.0 % |

**Carries at 60 fps (3 matches):** displayed carry speed matches real (median 2.51–2.59 vs
2.50–2.57 m/s real; was 2.18–2.26 in Realism); ball–carrier gap median 0.70 m (the fixed offset;
was 1.7–2.1 m, with a maximum equal to the whole carry length). Remaining over-9.5 frames around
carries are logged data anomalies (10–20 per match) and the cap itself; 4 carries in 2 matches have
a real anchor inside them that the straight path would violate — there the anchor wins and the ball
leaves the carrier. Largest single-frame jumps are now the half-time cut (by design).

**Structural statistics, LOMO tables, calibration, freeze-frame held-out error:** `BENCHMARKS.md`.

**Offside consistency on real StatsBomb passes (60 matches):** Pass Offside recipients drawn offside
69.3 % → 75.0 %; completed-pass recipients drawn offside 2.9 % → 2.6 %.

## T7 follow-up — speed-spike sweep (after the "clean" QA pass, before final)

The first full-season QA pass after T7 showed 0 anchor misses and 0 active-count mismatches, but a
dedicated max/p99-speed sweep (below) found three more real bugs, all now fixed:

- **D13 — dedup kept the less confident duplicate.** When two anchors are true duplicates (same
  position, confirmed 0.0 m apart — a carry's own `end_location` and the very next event starting
  there), `resolve_collisions`'s pass-1 dedup kept whichever sorted first, with no regard for which
  one carried the softer `event_offcam` (1.5 m Kalman variance) tag vs a normal on-camera anchor
  (0.05 m). Traced directly in the smoother (`common/smoother.py::smooth`): the surviving anchor's
  observation row showed `var=[2.25, 2.25]` (off-camera) instead of `0.0025` (hard), so the Kalman
  update only partially pulled the estimate onto a position both sources already agreed was exact —
  leaving the reconstruction curve's knot ~0.9 m from the true value at that instant, while the
  carry's own drawn path (read straight from the real event, bypassing the anchor system) reached
  the true endpoint exactly. The 1-frame handoff between the two was the spike. Fixed:
  `resolve_collisions` now keeps the on-camera tag when duplicates disagree only on confidence.
- **D14 — a StatsBomb source-data quirk: reset timestamps.** Found via the worst-frame list, then
  confirmed systematic: the last `Ball Receipt*` of a period (immediately before Half End) sometimes
  has its own `timestamp`/`minute`/`second` reset to ~00:00:00 while `period` and stream `index` stay
  correct — every neighbour by index sits at 45-49 minutes. Season-wide scan, **every event type**
  (`tests/analysis/timestamp_anomaly_full_scan.py`, reading the raw cached JSON directly, before any
  fix): **130 events across 130 of 380 matches** (34%) — 129 `Ball Receipt*`, 1 `Pressure`; 129 in
  period 1, 1 in period 2. Sorting a player's anchors by this corrupted declared time placed the
  event next to unrelated early-match anchors instead of its true neighbours, producing a spurious
  implied speed up to ~180 m/s. Fix (`loader_statsbomb.py`, right after computing continuous `t`):
  each such event's true time is bracketed between the previous event by stream index and the next
  one whose own time is not itself anomalous (Half End, in every case found). Two candidate times are
  tried in order — (1) `prev_t + prev event's own recorded duration` (exact when the previous event
  is the Pass this is the reception for — a real field, nothing invented), (2) the latest moment in
  the bracket (most charitable — minimum implied speed). Whichever keeps implied speed at or under
  9.5 m/s (the same cap used everywhere else) is kept and logged as retimed; if neither fits even
  the most charitable placement, the location is dropped (never guessed) — logged, and the event
  still appears in the feed/stats, just not as a position anchor. Season-wide result: of 130
  anomalies, **90 retimed** (47 via the precise duration-based method, 43 via the bracket-end
  fallback) and **40 dropped** as genuinely unrecoverable.
- **D15 — residual mid-segment PCHIP overshoot near the speed cap.** Even with D11's clamped
  endpoint tangents, a Hermite segment between two real anchors that are themselves close to 9.5 m/s
  apart could still bulge past it in the segment's *middle* (found via the season sweep: anchors
  implying 8.2-9.5 m/s, displayed 30-60 m/s at the worst points — Koné, Rose, Gestede, Gazzaniga,
  Surman among them). Fixed the same way the old, pre-round viewer did but this rewrite had dropped:
  `makeCurve` samples each segment's Hermite velocity at 5 points; if the peak exceeds 12 m/s, that
  segment falls back to plain linear interpolation, which cannot exceed the endpoint-to-endpoint
  average speed by construction. Both curves still hit every anchor exactly at s=0/1 either way, so
  this never moves a real position, only how the segment gets there.

**Verification**: all five named cases (Koné 3754132, Rose 3754268, Gestede 3754017, Gazzaniga
3754218, Surman 3754037) — their original 33-55 m/s spikes are gone; per-match max non-anomaly speed
is now 11.7-12.1 m/s in all five, anchor misses 0. Season-wide max/p99 numbers after the full rebuild
are in the final report (BENCHMARKS.md / final summary), not restated here since the full 380-match
QA was re-run after these fixes.

## Final season-wide speed sweep (after D13/D14/D15, all 380 matches)

| | realism | tactical |
|---|---:|---:|
| per-match max non-anomaly speed: max | 16.73 m/s (match 3754024) | 16.73 m/s |
| p99 | 14.25 | 14.37 |
| p90 | 12.57 | 12.21 |
| median | 11.87 | 11.85 |
| anchor misses (of 1,110,041 checked) | 0 | 0 |
| active-count mismatches | 0 | 0 |

Down from the pre-D13/14/15 max of 234 m/s (Alberto Moreno, D9/D12) — a ~14x reduction. Every real
anchor is still hit exactly (0 misses, worst-miss 0 m).

**Residual above 12 m/s, not yet fixed**: top-30 worst frames season-wide break down as 17
"reconstruction between anchors" (real anchors imply 5-9 m/s, displayed 12-15 m/s — the same
mid-segment PCHIP class D15 targets; a few slipped past the 5-point peak sampling), 11 "kickoff soft
anchor" (e.g. Andros Townsend, 3754024, t=2461s: real anchors imply 1.2 m/s, displayed 16.7 m/s), and
2 right at the "bracketing anchors imply it" cap (14.9 vs 9.5 m/s implied). The kickoff cases are not
reached by D15, which patches the *viewer's* spline — the excess speed there is already present in
the Python-side Kalman/OU output at the kickoff soft-anchor transition, before it becomes a payload
knot. Not fixed in this pass; flagged for the next one.

## Anchor collision audit: the 91.55 m case, and full distance breakdown

Asked directly: what is the 91.55 m "largest merged distance," and does the "true duplicate" rule
ever fire on anchors that are actually far apart? Full audit, all 638,485 logged collision events,
`tests/analysis/collision_distance_audit.py`:

| type | count | min (m) | median (m) | max (m) |
|---|---:|---:|---:|---:|
| `collision_duplicate_dropped` (true duplicate — position kept unchanged, later copy discarded) | 580,633 | 0.00 | 0.00 | **0.00** |
| `collision_retimed` (real position kept, time shifted to the earliest physically possible moment) | 57,202 | 0.00 | 1.49 | 89.26 |
| `collision_dropped` (no valid retime window — excluded entirely, nothing kept) | 650 | 0.09 | 2.97 | **91.55** |

**The "true duplicate" rule never fires on anchors more than 0.05 m apart** — 0 of 580,633 violate
this, confirmed directly. Earlier wording ("largest distance among any merged/retimed/dropped pair")
conflated three different operations under "merged"; corrected below.

**The 91.55 m case is a drop, not a merge** — nothing was combined. John Stones (Everton), Everton
2–1 Swansea City, 2016-01-24 (match 3754163): his own Pass at t=5802.285s from (88.4, 0.1) — near the
opponent's corner — is followed 3.24 s later by his own Ball Receipt*/Carry at t=5805.525s at
(15.3, 55.3) — his own defensive corner. Implied speed 28.3 m/s (~3x human sprint capability). No
retime window fit, so the later location was excluded (not guessed).

**Distances over 1 m / 5 m** (any type): >1 m: 37,551 (5.9 % of all logged events — 36,992 retimed,
559 dropped); >5 m: 8,699 (1.4 % — 8,453 retimed, 246 dropped). All in the retime/drop categories,
none in true-duplicate.

No fix needed — the specific rule audited here (true-duplicate distance) behaves exactly as
specified. Open design question, not acted on without instruction: 36,992 retimes shift a real
position's *time* by more than what 1 m of extra travel distance would need, up to the 89.26 m/9.4 s
worst case — i.e. "trust the position, assume it happened later than recorded" rather than "distrust
the position." That was the original spec's deliberate choice (retime before dropping); flagging it
here as a knob that could be tightened (e.g. a distance cap beyond which drop is preferred over
retime) if wanted, not changing it unprompted.
