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

## Polish round — pacing, the drawn ball, stoppages, crowded duels, pass height

Requested after watching the final build. Tested on Arsenal v Liverpool (3754129), Aston Villa v Man
City (3754258) and Norwich v Liverpool (3754348) before the season rebuild. "Before" = the build at
the start of this round: viewer snapshot `tests/baseline/app_before_polish.js`, payloads kept outside
the repo (`~/.cache/football_anim/polish_before/`). Evidence: `tests/evidence/polish/`. The
reconstruction itself (model, smoother, every player's knots) is **unchanged**: rebuilt payloads
differ from the old ones only by the new event/stoppage fields (checked: max position change 0.0 m,
tracks and model ball path identical).

### P1 — "super dash": diagnosis

**Speed each real gap requires if the animation must cover it in exactly its real time**
(`tests/analysis/gap_speeds.py`, all 380 matches, payload anchors as displayed; caps from P1-caps):

| gap | count | needs more than the cap |
|---|---:|---:|
| off-ball: consecutive anchors of one player (cap 8.0 m/s) | 859,826 | **4.6 %** (5.8 % excluding shot-freeze-frame pairs) |
| &nbsp;&nbsp;of which gaps < 1 s / 1–3 s / 3–10 s / > 10 s | 94k / 88k / 107k / 571k | 37 % / 3.9 % / 0.7 % / 0 % |
| with the ball: StatsBomb carries (cap 7.5 m/s) | 276,949 | **8.5 %** (p99 implied speed 44 m/s) |
| ball flights: pass/shot start → end over its duration (cap 30 m/s) | 652,192 | **1.6 %** |
| ball handoffs: one ball event's end → the next's start (cap 30 m/s) | 236,895 over 0.5 m | **55 %** — 52 % have **zero** time between them |

**What the viewer drew** (60 fps, Tactical, 3 matches — `tests/drivers/polish_diag.js`,
`dash_find.js`): some player above 8 m/s in 1.7–2.0 % of all frames; 117–149 "super dash" episodes
per match (≥ 0.15 s above 8 m/s): ~50 % carries, ~30 % reconstruction between anchors, ~20 % off-ball
runs to receive a pass; ball faster than 30 m/s in 1,487–1,665 frames per match.

**Shared cause?** Partly. The player dashes and fast carries are the forced 1:1 mapping (a real gap
too short for its distance) plus the curve/carry drawing spending that speed unevenly. The ball
"teleport" is mostly *not*: the 52 % zero-time handoffs cannot be fixed by any pacing (no time to
stretch) — they are StatsBomb recording two different locations at one instant (a pass end and the
Ball Receipt\* 3.2 m away; an "Incomplete" receipt for the intended receiver followed by the
interceptor) — and the "ball stays still, then jumps" case was the ball path parking the ball at a
receipt while the player moved on (P2). So P1 and P2 got separate fixes.

**P1-caps — speed caps, from the real tracking used elsewhere in this project**
(`tests/analysis/speed_caps.py` → `speed_caps.json`; Metrica ×3 + DFL/IDSSE ×7, 1 s windows; the
maxima are tracking glitches):

| | p50 | p90 | p99 | p99.9 | chosen cap |
|---|---:|---:|---:|---:|---:|
| outfield running (5.7 M windows) | 1.46 | 3.84 | 6.21 | 7.90 | **8.0 m/s** |
| carrying the ball (49,846 windows) | 2.24 | 5.39 | 7.50 | 8.51 | **7.5 m/s** (p99: carrying is not faster than sprinting; the p99.9 rests on ~50 windows and includes chasing a loose ball) |
| ball | 3.20 | 11.99 | 19.58 | 30.55 | **30 m/s** |

Real p99.9 running speed falls with the window length (7.9 m/s over 1 s, 6.9 over 5 s, 5.5 over 10 s),
which is why short gaps are the problem: 37 % of sub-second gaps need more than 8 m/s, almost none
of the long ones.

### P1 — fix: pacing (`viewer/app.js`, "pacing")

Playback no longer maps match time 1:1. Per display mode, S(t) = playback seconds per match second
on 0.05 s bins, computed from what is actually drawn (every player, the ball):
- **stretch**: max over everything drawn of on-screen speed / its cap (carry cap for the player on
  the ball). Anything faster than its cap makes that moment take longer; max-filtered over ±0.5 s
  and blurred inside that window so it eases in and the cap still holds everywhere. Fast things are
  re-sampled at 4 points per bin (a curve can peak ~15 % above its bin average).
- **compress**: only sustained idle play (every player under 4 m/s and the ball under 12 m/s for
  ±2 s; the real p90s) may run faster, at most 1.5x.
- **stoppages**: skipped separately (P3).
- The user's 0.5x–10x multiplies on top. Nothing is reordered and no location moves: positions are
  still a pure function of the match clock, the clock only advances at a varying rate, and it is
  exact at every real event. Computed lazily in 15 s chunks (~20 ms each) by a background filler.
- Carries are now drawn between their own start / carry_end anchor times (which equal the event
  times unless D9 re-timed an impossible one) at their real mean speed; the old "anomaly" path that
  drew them at 9.5 m/s and arrived late is gone — pacing slows them instead. A carry whose start or
  end anchor was dropped is left to the reconstruction.
- A handful of pathological inputs (e.g. a 20 m pass recorded as lasting 0.01 s) would need > 3x
  slow motion over a whole second; those flights are lengthened to need exactly 3x (4–5 per match,
  logged as `extreme_flights_lengthened`), the same "trust the location, move the time" rule as D9.

**Five former super dashes, speed over playback time** (`tests/evidence/polish/dash_before_after.png`,
`dash_series_*`):

| example | match | peak before | peak after | dash in playback, before → after |
|---|---|---:|---:|---|
| 1 Carry (Monreal): 35.7 m carry recorded as 1.7 s | 3754129 | 9.5 | 7.5 | 3.75 → 4.76 s (+27 %) |
| 2 Carry (Firmino): 4.3 m carry recorded as 0.29 s | 3754348 | 94.1 | 7.5 | 0.30 → 0.38 s (+27 %) |
| 3 Off-ball run to receive (Ibe) | 3754129 | 12.0 | 8.4 | 1.38 → 2.02 s (+46 %) |
| 4 Off-ball run to receive (Sinclair) | 3754258 | 11.6 | 8.0 | 1.42 → 1.99 s (+40 %) |
| 5 Reconstruction between anchors (Veretout) | 3754258 | 10.9 | 8.0 | 3.38 → 4.44 s (+31 %) |

(Example 2's window is the 0.3 s of the old spike; the carry itself now spans its anchors' times.)

**Three matches, after** (`diag_after.jsonl`, 60 fps of playback, stoppage skips excluded, caps +1 %):
frames with any player over 8 m/s 1.7–2.0 % → 0.01–0.09 %; player frames over 9.5 m/s → 0; ball
"still, then jump" 14–30 per match → 0. Full QA per match (`qa.js`): 12–22 player-frames of ~5 M over
their cap (worst 9.4–11 m/s, single frames), 0 ball frames over 30 m/s, 0 anchor misses.

### P2 — ball stays still, then teleports

Not a side effect of P1 (see the diagnosis). Checked separately in the four situations named
(`tests/drivers/ball_situations.js`, 3 matches):

| situation | windows (3 matches) | before | after |
|---|---:|---|---|
| carry with no end location or duration | 0 | — (StatsBomb carries always have both here) | — |
| gap between a carry's end and that player's next event (> 0.3 s) | 0 | — (the next event starts where the carry ends) | — |
| Ball Receipt\* then the same player's next event, no Carry | 68 | ball parked at the receipt, moving up to 43 m/s to catch up; ball–player gap median 0.9–2.0 m, p90 up to 14 m | ball on the player's path the whole time: gap 0.69 m median (the dribble lead), no step over 30 m/s |
| reception window (pass in flight until 0.5 s after the receipt) | 2,390 | **80 %** contain a > 30 m/s ball jump (median worst step 42 m/s, p99 100–120) | **0** (3754129; the other two are in `ball_situations_after.jsonl`) — ball–receiver gap at the receipt 0.0 m |

Fix: the drawn ball is its own model (`buildBallModel`), a chain of segments each starting where the
previous one ended (so it cannot jump): flights over the real duration; on a player's drawn path
through carries **and any gap between two on-ball events of the same player** (eased from the
recorded location to the next one, 0.7 m lead in the running direction faded at both ends); held by
a receiver/recoverer until the last moment before someone else's event; rolling at constant speed
after a release. Real-data conflicts, all logged in `ballStats`: Ball Receipt\* "Incomplete" (the
intended receiver — the ball never reached him) and keeper events that don't touch the ball (Shot
Faced, Goal Conceded, Penalty Conceded) are not knots; a flight followed within 0.5 s by another
on-ball event ends at that event (1,079 of 1,145 flights in 3754129; for most this is the same point,
but in the cases above the pass end and the receipt are recorded at one instant ~3 m apart, and the
receiver is where the ball goes); a single touch
recorded at the same instant as the ball was elsewhere is reached distance/30 m/s later (max 0.5 s;
81 in 3754129, max 0.18 s). The model's own ball input (`common/ballpath.py`) is untouched — this is
display only. Continuity check (`qa.js`, every segment boundary): max step 0.06 m.

New payload fields: `receiptOutcome` (Ball Receipt\*), `gkType` (Goal Keeper), `passHeight` (Pass).

**Found only by the season-wide QA, after the 3-match evidence above was captured (P1-4 QA reruns
below use the fixed version): two remaining ball bugs**, both in how the ball model's knots are
timed, not in the payload:
- **Wrong knot far in the future.** `anchorTimeNear` (used to align a knot's time with a D9-retimed
  player anchor) searches up to 5 s forward. A Carry's end and the very next event's own start often
  coincide exactly and get merged into one anchor by `resolve_collisions`; when that merged anchor
  was itself retimed (a genuinely too-fast recorded carry), the FOLLOWING event's own knot — built
  from its raw, unretimed time — matched that same far-future anchor by position, silently pushing
  its own start seconds ahead, past other real events, corrupting the knot order. Example (match
  3754200, Herrera→Rooney, t≈1147–1150 s): Herrera's real 0.08 s/24 m carry was retimed to end 3.0 s
  later; his following Pass's own knot then also snapped to that same instant, sorting it after
  Rooney's entire subsequent touch → carry → shot, dropping the pass's flight (its computed duration
  went negative) and producing a same-instant 900+ m/s hop to patch the gap. Found on **101 of 380**
  matches (onscreen ball speed >40 m/s), worst 2107 m/s. Fixed: anchor-snapping in the ball model is
  now applied **only to Carry knots** (the one case tied to `tr.carryObjs`, which needs it for the
  same reason); every other on-ball event uses its own raw recorded time, matching
  `common/ballpath.py`'s own authoritative ball proxy.
- **Instant hop into a flight's own start.** The "touches reached late" hop protection (stretching a
  too-short real gap to distance/`BALL_CAP_MPS`) only applied when the destination knot was a plain
  touch, not when it was itself the start of a flight or carry — so a same-player double-touch
  0.003 s apart, 2.7 m apart, landing right before a Shot, still spiked to 98.6 m/s. Fixed by
  dropping that restriction; the delay applies to any gap regardless of what it leads into.

Verified on the 4 matches found above (3754129, 3754258, 3754348, 3754200): all now sit right at the
30 m/s cap (30.1–30.3 m/s) with discontinuities under 0.08 m (was up to 2107 m/s / 3.51 m on
3754200). Season-wide re-run across all 380 matches in progress; final numbers in the season QA
summary below.

### P3 — stoppages

**Detection fixed first.** Stoppages were labelled from `play_pattern`, which describes the whole
possession: a pause *inside* a possession that began with a throw-in was also "Throw-in". On 7
matches ~15 % of labelled gaps were such pauses (e.g. Norwich v Liverpool showed 12 "goal" restarts
for 9 goals). Now a stoppage is a ≥ 5 s gap ending in a **set-piece restart as StatsBomb types the
restart event itself** (`pass.type` Throw-in / Corner / Goal Kick / Free Kick / Kick Off, `shot.type`
Free Kick / Penalty) — this coincides exactly with "new possession" on those 7 matches. A free kick is
refined to **foul** or **offside** when a Foul Committed/Won or an offside (Pass outcome "Pass
Offside" or an Offside event) happened in the 6 s before the gap. Each stoppage now carries the
restart team, spot (home frame) and taker, and `out_t` (when the ball actually went dead: the last
event's own end, e.g. a pass arriving out of play). No merging (each has its own restart).

**Sequence on screen** (both display modes; pure function of the match clock):
1. the real play into it at normal pacing, including the ball flying out when a pass/shot took it
   there; at `out_t` a pulse at the dead-ball point (white out of play, yellow foul/offside/penalty,
   gold goal) and the restart banner eases in;
2. `out_t` + 1.0 s → restart − 1.2 s: the dead time is **skipped in 1.6 s of playback**, easing in and
   out of the fast-forward, pitch dimmed, a "⏩ skipping 0:29 of stoppage" chip with a progress bar;
   the ball is carried to the restart spot;
3. the last 1.2 s before the restart at normal pacing, the taker ringed (dashed, team accent colour);
4. the restart, then the banner eases out 1.6 s later. Banner: team-coloured stripe and border,
   e.g. "Throw-in — Arsenal", "Foul — Arsenal free kick", "Offside — Arsenal free kick", "Corner —
   Liverpool", "Goal kick — Arsenal", "Goal — Liverpool · Norwich City kick off", "Penalty — …".

Short stoppages (dead time under 3 s after the hold/lead-in) keep the banner but are not skipped.

**Five types on Arsenal v Liverpool** (`tests/evidence/polish/stoppage_<kind>_{before,after}.png`,
7 frames each, real Chrome from `file://`):

| type | real dead time | whole sequence (approach → banner gone) in playback | what's on screen |
|---|---:|---:|---|
| foul (Milner on Monreal) | 21.9 s | 7.2 s | Monreal's carry, yellow pulse at the foul, "Foul — Arsenal free kick", dimmed skip, Monreal ringed at the spot, the free kick |
| throw-in (Škrtel pass out) | 31.6 s | 7.0 s | the pass rolling out at the touchline, white pulse there, "Throw-in — Arsenal", skip, Monreal ringed with the ball on the line, the throw |
| corner (Firmino shot, Čech save) | 64.6 s (includes Coquelin's injury) | 9.3 s | the shot and save (slowed: a fast approach), "Corner — Liverpool", "skipping 1:02", ball at the flag, Milner ringed, the corner |
| goal kick | 25.9 s | 7.4 s | ball out, "Goal kick — Arsenal", skip, ball in the six-yard box, the kick |
| offside (Can) | 28.8 s | 7.0 s | the offside pass and reception, yellow pulse, "Offside — Arsenal free kick", skip, the free kick |

Before, the same moments showed a small yellow "⏩ Foul — free kick" (no team) for the whole
warped interval, which started at the last event — so the pass going out was itself skipped — with
no dimming, no restart spot/taker, and a hard cut at the restart.

**Limit, stated plainly**: StatsBomb gives no out-of-play location for a clearance, block or save.
Across the 3 matches ~60 % of throw-ins, ~75 % of goal kicks and ~65 % of corners are entered via a
real flight (the ball is seen going out); the rest show the ball where it was last touched until the
skip carries it to the restart (`stoppage_entry.jsonl`). Fouls, offsides, goals and penalties always
have a real incident location.

### P4 — crowded duels

Diagnosed on 3 moments of Arsenal v Liverpool (`tests/drivers/cluster_find.js`): the corner that
Milner delivers (match clock t = 4760 s), a midfield duel (Ramsey, x = 41 m, t = 3569 s), and a
tackle in the box (Can, x = 97 m, t = 5098 s). Both things were happening: (a) a #1-style speed problem — players around the ball
reached 9.4–9.5 m/s and the ball jumped (2–4 frames > 30 m/s per moment); (b) a rendering one —
fixed-size tokens covered each other (57–95 % of frames had overlapping tokens) and nothing said who
had the ball.

Fix: (a) is P1/P2 (after: 0 ball jumps, nearby players ≤ 6.0–7.5 m/s on screen). (b) The player on
the ball (owner intervals from the ball model: carrier, holder, same-player gap) gets a soft white
halo that cross-fades over 0.15 s when possession changes, and is drawn on top of the cluster.
**A positional push-apart was tried and rejected**: in tight clusters its push direction flipped as
players passed within centimetres of each other, drawing single-frame 30–60 m/s twitches (measured)
— exactly what this round removes. **Shrinking tokens to fit their nearest neighbour was also tried,
then reverted on watching the built video** — it read as an unwanted "avoid clutter" effect rather
than clarifying who has the ball, so token size is back to the original fixed 9 px (12 px active) in
both modes; only the halo and draw-order changed. Before/after crops: `clusters_before_after.png`
(from the shrink-to-fit version — tokens there are smaller than what actually shipped; the halo and
draw-order are what carried over).

### P5 — ground vs lofted passes (kept)

`pass.height` (Ground / Low / High) was parsed in an earlier round but removed as dead code (T1f); it
is parsed again and shipped per pass. A lofted pass is drawn raised above a ground shadow by
h(f) = 4·H·f·(1−f) over the flight fraction f, with H the projectile apex for the pass's **own real
duration**, H = g·T²/8 (a 65 m keeper kick lasting 3.66 s → 16.4 m); High Pass at least 1.8 m (above
shoulder, StatsBomb's definition), Low Pass at most 1.5 m (below shoulder), Ground Pass flat.
Nothing is invented beyond that: the ground position, timing and every recorded location are
identical for all heights; only the drawn ball is lifted (0.4 px of lift per pitch px), with a faint
vertical line from the shadow and a dashed ground trail for high passes. It reads clearly in the
screenshots (`pass_height.png`), so it is kept. Arsenal v Liverpool: 225 high, 167 low, 718 ground passes.

### P6 — off-ball reconstruction still looked like a dash (post-release fix)

Reported after watching a real build: a player would start moving, ramp up fast,
hold a high pace for a while, then stop abruptly right where the event data
places their next touch — distinct from carries (fixed in P1/P4 above), which
already ease in and out. Root cause: pacing (P1) caps *peak* on-screen speed by
stretching wall-clock time, but never reshapes *how* a player accelerates —
the underlying reconstruction curve's own velocity profile (from
`common/smoother.py`'s per-segment Kalman/OU fit) can ramp onto and off a
required speed abruptly, and stretching time uniformly preserves that shape,
just at lower absolute speed.

**Fix (`viewer/app.js`, "per-player timing along the drawn path"):** a second,
independent re-timing layer, applied only to the plain reconstruction curve
(`baseAt`) — carries and Tactical reception runs already have their own
correct, capped S-curve profile (P1/P4) and are exempt, checked at the real,
un-retimed time. For the reconstruction, each player's path between two
consecutive *fixed* points (every hard real anchor, on-pitch interval /
segment / period edges, and every carry/reception-window boundary) keeps its
exact route and its exact position at both fixed points; only *when* along
that route he is at a given moment changes. Target speed is the
reconstruction's own speed, Gaussian-smoothed (σ=0.6 s), bounded near each
fixed point by what the neighbouring stretch allows accelerating/decelerating
into at `RT_ACCEL_MPS2 = 3.5` (real tracking: 1 s speed changes p99 2.6,
p99.9 4.1 m/s², `tests/analysis/accel_caps.json`); the stretch's own path
length is then matched exactly with a smoothstep-shaped correction that
vanishes at both fixed points, so speed stays continuous through every real
anchor instead of snapping.

**Two bugs found building this, both from QA before shipping:**
- **Wrapping carries too.** The first version applied re-timing to
  `playerPositionAt`'s full output, including carries — re-pacing an
  already-correctly-paced profile on top of itself pushed carry speeds to
  13–16 m/s, past their 7.5 m/s cap. Fixed by checking carry/anticipation
  first, at the real t, and re-timing only the reconstruction fallback.
- **Missing window boundaries.** A reception-anticipation window's start
  (`t_pass`) belongs to the *passer's* timeline, not the receiving player's
  own anchors, so it was never a wall the re-timing knew to stop at. The
  reconstruction could drift metres from the raw curve leading up to that
  instant, then snap to the anticipation run's exact defined start the
  moment the window opened — found via QA as a 243 m/s on-screen spike
  landing exactly on a `t_pass`. Fixed by also registering every carry's and
  reception window's own start/end as a wall.
- **Performance, twice.** The re-timing math's control points were first
  built on a fixed 0.1 s grid — fine for short gaps, but a stretch can be
  several minutes long (a player is off any anchor 77 % of the time,
  BENCHMARKS.md), so this made one match's worth of re-timing take 19 s to
  build and stutter live playback. Capped control points per stretch at 120
  regardless of duration (the final position lookup is still continuous, so
  a coarser internal grid is invisible once smoothed). That still left the
  *target-speed* smoothing itself scanning a fine grid inside its window,
  whose cost scaled with total match duration touched, not window size —
  full QA on one match went from a few seconds to a 3+ minute hang. Replaced
  with a fixed 12-sample window average, independent of duration. Combined:
  per-match diagnostic QA time roughly 1.5x of the pre-P6 baseline; live
  background pacing computation (what actually matters for playback
  smoothness) unaffected (~31 ms per 15 s chunk, same order as before).

**Verified (3754112, 3754129 — `tests/drivers/qa.js`, `accel_diag.js`):** 0
anchor misses both modes on both matches (unchanged), ball still capped at
~30 m/s, carry speeds back to their normal range (8.98–9.43 m/s, matching the
pre-P6 baseline exactly), on-screen player speed correctly bounded again
(9.14–9.62 m/s max, was 243/271/135/124 m/s with the two bugs above still
in). Acceleration (match 3754112, Tactical, real p99.9 acceleration = 4.06
m/s² is the target from real tracking):

| | p50 | p90 | p99 | p99.9 | share over real p99.9 |
|---|---:|---:|---:|---:|---:|
| before P6 | 0.30 | 1.01 | 2.26 | 4.29 | 0.137 % |
| after P6 | 0.27 | 0.87 | 2.16 | 4.13 | 0.109 % |

A smaller improvement than an earlier (buggy) measurement suggested — once
carries are correctly excluded from this layer and the window-boundary jumps
are fixed, most of the season's harshest accelerations turn out to already be
inside carries (handled separately) rather than plain reconstruction. Drawn
position vs the raw (non-re-timed) curve at the same real time: median 0.14 m,
p90 0.53 m, p99 1.6 m, max ~23 m on the longest unobserved stretches — the
same points along the same path, redistributed in time; no new position error
since the raw position there was already just the model's regression-based
guess (already uncertain, shown as such in Full Realism).

### Changed behaviour to be aware of

- **Playback length now varies.** At 1x a match takes ~60–70 % of its match-clock length (Arsenal v
  Liverpool 4,002 s of 5,706 in Tactical; before: 3,762 s in Tactical, which already warped its
  stoppages in 1.5 s each, and 5,706 s in Full Realism, which did not). Breakdown for that match:
  stoppage skips save 1,637 s, idle compression saves ~150 s, slow-downs add ~81 s. BENCHMARKS.md
  "Playback pacing" has the season numbers.
- **Full Realism now also skips stoppages and paces** (before, only Tactical warped stoppages). Both
  modes still show the same reconstruction; they now differ only by knot thinning, pass anticipation
  and uncertainty rings.
- The browser test's 10 s playback check is now "5–25 s of match clock" (was ≥ 8 s).
- **`tests/drivers/qa.js`'s own diagnostic runtime is now ~1.5x per match** (P6's re-timing layer
  adds real, if bounded, cost to an exhaustive 60 fps sweep of every player's whole match — see P6's
  "performance, twice"). Season-wide (`pipeline/qa.py`, 12 parallel workers) is estimated at
  roughly 70–90 minutes rather than the ~20–25 minutes quoted before P6; not yet re-run season-wide
  at time of writing. Live playback itself is unaffected (background pacing chunk cost ~31 ms per
  15 s of match, same order as before P6) since it never needs that exhaustive a sweep.
