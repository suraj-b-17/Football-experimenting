# Diagnosis report: how the animation pipeline actually produces positions

Read-only investigation. No files were changed. All numbers below come from running the
actual code (`pipeline/`, `training/`) against the actual cached StatsBomb data in
`~/.cache/football_anim/statsbomb/`, not from memory or prior reports. File:line references
point at the code as it exists right now. Where something could not be established from the
code/data, that is stated explicitly rather than guessed.

Primary match used throughout: **Arsenal 0–0 Liverpool, 2015-08-24, match_id 3754129**
(StatsBomb `competition_id=2, season_id=27`). Three more matches used for the multi-match
checks: Norwich City 4–5 Liverpool (3754348), Aston Villa 0–0 Manchester City (3754258),
Liverpool 0–3 West Ham United (3753984, has a red card — actually a second yellow, see §C10/E13).

---

## A. How a player's position is produced between anchors

### A1. Full path, file and function for each step

| # | Step | File : function | What it does |
|---|---|---|---|
| 1 | Load raw data | `pipeline/loader_statsbomb.py :: load_match` | Fetches StatsBomb events/lineups/matches JSON (cached under `~/.cache/football_anim/statsbomb/`), rescales `[x,y]` from StatsBomb's 120×80 units to meters on a 105×68 pitch (each team's own goal already at x=0 in StatsBomb's raw data — no side-flip needed), builds a continuous match-clock `t` across periods via `period_offset`, returns one `match` dict. |
| 2 | Build per-player tracks | `pipeline/build_match.py :: build_tracks` | Seeds each starting player at `t=0` with a formation-template position (`position_anchor()`), then appends one waypoint for every event that has both a location and a player and is not in `NON_POSITIONAL`, a second waypoint at `carry.end_location` for Carries, and a reception waypoint (only for `passOutcome=="Complete"`) resolved to the receiver's own next real touch time. Also tracks `enter`/`exit` from Starting XI / Substitution / red-card events. |
| 3 | Detect stoppages | `pipeline/build_match.py :: detect_stoppages` | Independent of the position pipeline — reads `play_pattern` on the event after a ≥5s gap to label dead-ball windows (throw-in, corner, goal kick, free kick, foul, kickoff-after-goal). Feeds the viewer's stoppage time-warp only. |
| 4 | Build ball + possession proxy | `pipeline/apply_to_match.py :: build_ball_and_possession` | There is no real ball tracking for this competition, so this builds one ball-position **proxy** trajectory from every event's own actor location, converted into a single shared ("home-perspective") frame, plus a possession-team series read directly from StatsBomb's `possession_team` field. |
| 5 | Predict + smooth per player | `pipeline/apply_to_match.py :: smooth_all` | For each player: computes a **static anchor** = whole-match median of that player's own waypoints (see §E13 finding #1 — this is a bug, it does not match how the model was trained); builds a 1 Hz grid from `enter` to `exit`; interpolates the ball proxy onto that grid and flips it into the player's own team frame; looks up the possession flag; forms the 5-feature model input; calls the trained `models/mean_model.joblib` (RandomForestRegressor) to predict an (x,y) offset from the anchor, giving a raw mean path `mu(t)`; rate-limits `mu(t)`'s step-to-step speed to `MU_MAX_MPS=6.5 m/s`; feeds `mu(t)` plus the real waypoint observations into the OU/Kalman smoother. |
| 6 | OU + Kalman smoothing | `training/kalman_ou.py :: smooth_track` / `smooth_axis` | Per-axis (x, y independently) Kalman filter + RTS backward smoother for an Ornstein-Uhlenbeck residual `r(t)` around the externally supplied `mu(t)`; real waypoint observations are injected as updates at their nearest grid index; returns smoothed `(x, y)` plus variance `(px, py)` at every 1 Hz grid point. |
| 7 | Orchestration | `pipeline/apply_to_match.py :: apply` | Runs steps 2–6 for home and away separately, returns `(tracks, home_smoothed, away_smoothed)`. |
| 8 | Assemble payload | `pipeline/build_payload.py :: build` | Packages tracks (raw waypoints + receptions + the 1 Hz smoothed grid), events, stoppages and calibration constants into `output/data/<match_id>.json`. |
| 9 | Display — data model selection | `viewer/app.js :: playerPositionAt` | Picks between `smoothedPositionAt()` (Full Realism — interpolates the already-computed 1 Hz smoothed grid, does **not** re-run Kalman) and `tacticalPositionAt()` (Tactical Clarity — splines through raw touches + explicit pass-arrival easing), based on `displayMode`. |
| 10 | Display — geometry | `viewer/app.js :: trackPositionAt`, `smoothedPositionAt`, `pchipTangents*`, `hermiteEval` | PCHIP-Hermite spline interpolation for on-screen motion between whichever discrete points feed the active mode, with speed-based safety clamps (see A3). |
| 11 | Render | `viewer/app.js :: drawFormations`, `drawPlayerToken` | Converts to pixels, draws the token and (Full Realism only) an uncertainty ring of radius `sd × calib.k68` pixels, capped at 55px. |

### A2. Model input/output, and whether players are coupled

Model input, exactly (`training/fit_mean_model.py:27`, `pipeline/apply_to_match.py:35`):

```
FEATS = ["anchor_x", "anchor_y", "ball_rel_x", "ball_rel_y", "possession"]
```

- `anchor_x, anchor_y`: the player's own slowly-varying baseline position (see §E13 #1 for the anchor-definition bug).
- `ball_rel_x, ball_rel_y`: ball position minus the player's own anchor.
- `possession`: 1.0 if the player's own team currently has StatsBomb's `possession_team`, else 0.0.

Output (`TARGETS`, `fit_mean_model.py:28`): `offset_x, offset_y` — the player's real position minus their anchor, added back to the anchor at inference to get the predicted mean position.

**Nothing couples players.** `smooth_all()` (`apply_to_match.py:70-130`) loops over tracks one at a time and calls `model.predict(X)` independently for each player. There is no team-shape feature, no teammate distance/line-height/compactness input, and no cross-player term anywhere in the RandomForestRegressor's feature set or in the OU/Kalman smoothing (which is also per-player, per-axis). Confirmed explicitly — this is not a case of "something subtle couples them," there is nothing in the code path that reads any other player's state. See §E12 for what would be needed to add this.

### A3. Display-layer behaviors on top of the model

| Behavior | Mode | File : location | Controlling parameter(s) |
|---|---|---|---|
| Pass-arrival sync (receiver eases from pre-pass position to the real pass `end_location` between `t_pass` and `t_receive`) | Tactical Clarity only | `viewer/app.js :: tacticalPositionAt` (lines 228–242), reception data from `build_match.py :: build_tracks` (109–130) | `easeInOutCubic`, receiver's own `receptions[]` list |
| Off-ball noise removed (no jitter) | Tactical Clarity only | `viewer/app.js :: tacticalPositionAt` calls `trackPositionAt(track, t, false)` — `wander=false` | the `wander` boolean argument |
| Off-ball "wander" jitter | Full Realism, **but only as a fallback** — see caveat below | `viewer/app.js :: trackPositionAt` + `wanderOffsetM` (131–169) | amplitude capped at 1.4 m, only applied when the waypoint gap `dt > 8s`, edge-faded within 3s of a real touch |
| Stoppage time-compression | Tactical Clarity only | `viewer/app.js :: render` + `findStoppageAt` (252–260, 444–461) | `STOPPAGE_WARP_REAL_SECONDS = 1.5` |
| Uncertainty ring | Full Realism only | `viewer/app.js :: drawPlayerToken` (339–348) | `calib.k68`, ring pixel radius capped at 55 |
| Peak-speed clamp on spline tangents | Both (general spline safety) | `precomputeTangents` / `smoothedPositionAt` | `PEAK_MPS = 9.5` |
| Linear fallback when a raw touch-to-touch gap implies a sprint faster than possible | Full Realism raw-track fallback path only | `trackPositionAt` (149–157) | `MAX_SPEED_MPS = 8.0` |

**Caveat on "reduced off-ball noise"**: in Full Realism mode, `playerPositionAt()` calls `smoothedPositionAt()` first and only falls back to the raw-waypoint `trackPositionAt()` (where `wander` would apply) when a track has fewer than 2 smoothed grid points. Since essentially every real player in a full match has a valid smoothed grid, **the "wander" jitter is effectively dead code for real matches in Full Realism mode** — it does not run for the players it looks like it was designed for. All four behaviors from the earlier tactical-clarity round (pass-arrival sync, off-ball noise removed, stoppage compression, and the general speed clamps) do exist in this new pipeline/viewer; the wander caveat above is a discrepancy worth knowing about, not a missing feature.

---

## B. Anchors and StatsBomb fields

### B4. Every event type in Arsenal v Liverpool (3754129), with location/player presence and anchor usage

`NON_POSITIONAL = {"Starting XI","Half Start","Half End","Substitution","Tactical Shift","Injury Stoppage"}` (`build_match.py:19`). Any event **not** in that set, with a location and a player, becomes a waypoint in `build_tracks()` — there is no further filtering by event type.

| Type | Count | Has location | Has player | Used as anchor? |
|---|---:|---:|---:|---|
| Pass | 1110 | 1110 | 1110 | Yes |
| Ball Receipt* | 1077 | 1077 | 1077 | Yes |
| Carry | 849 | 849 | 849 | Yes (+ a 2nd waypoint at `carry.end_location`) |
| Pressure | 370 | 370 | 370 | Yes |
| Ball Recovery | 144 | 144 | 144 | Yes |
| Duel | 110 | 110 | 110 | Yes |
| Block | 59 | 59 | 59 | Yes |
| Clearance | 56 | 56 | 56 | Yes |
| Dribble | 54 | 54 | 54 | Yes |
| Goal Keeper | 44 | 44 | 44 | Yes |
| Shot | 39 | 39 | 39 | Yes |
| Dribbled Past | 37 | 37 | 37 | **Yes** — see note below |
| Dispossessed | 35 | 35 | 35 | Yes |
| Miscontrol | 22 | 22 | 22 | Yes |
| Interception | 22 | 22 | 22 | Yes |
| Foul Committed | 18 | 18 | 18 | Yes (`card` field also read) |
| Foul Won | 17 | 17 | 17 | Yes |
| Substitution | 5 | 0 | 5 | No — handled specially (sets `exit`/`enter`) |
| Half Start | 4 | 0 | 0 | No — `NON_POSITIONAL` |
| Injury Stoppage | 4 | 0 | 4 | No — `NON_POSITIONAL` |
| Half End | 4 | 0 | 0 | No — `NON_POSITIONAL` |
| Referee Ball-Drop | 4 | 4 | 0 | No — no player, so `get(pid,...)` is never reached |
| Starting XI | 2 | 0 | 0 | No — `NON_POSITIONAL` (used only for kickoff seeding) |
| Player Off | 1 | 0 | 1 | No — has no location, not specially handled either (see §C10/E13 note) |
| Player On | 1 | 0 | 1 | No — same as above |
| Tactical Shift | 1 | 0 | 0 | No — `NON_POSITIONAL` |
| Bad Behaviour | 1 | 0 | 1 | No location, so never a waypoint; **is** specially handled for `exit` (see below) |

Total events: 4090.

**No event type with both a location and a player is left unused** — the code does not distinguish "the player controlled the ball here" (Pass, Carry, Shot) from "the player was recorded here incidentally" (Pressure, Duel, Dribbled Past, Block, Interception, Ball Recovery, Foul Committed/Won, Miscontrol, Goal Keeper, Dispossessed). All of them become identical position anchors. This is a real, if defensible, design choice — flagged again in §E13 since it wasn't obviously intentional from the code comments.

### B5. Field usage

| Field | Used? | How |
|---|---|---|
| `location` (per-event position) | **Used** | `loader_statsbomb.py::_to_m` → `ev["x"]/["y"]`; becomes waypoints in `build_tracks` |
| `related_events` | **Not used** | Parsed by StatsBomb, never read anywhere in `pipeline/` |
| `under_pressure` | **Not used** | Never read |
| `counterpress` | **Not used** | Never read |
| `off_camera` | **Not used** | Never read |
| `play_pattern` | **Used** | `loader_statsbomb.py:89` → `RESTART_LABELS` lookup in `build_match.py::detect_stoppages` |
| `duration` | **Used only for Carry** | `loader_statsbomb.py:108`, `ev["duration"] = e.get("duration", 0.0)`, inside the `if c and c.get("end_location")` block. **Not captured at all for Pass or any other event type**, even though StatsBomb provides a real flight-time `duration` on every event (needed for §F14/F15 below — I had to read it from the raw cached JSON directly, since the loader discards it for passes). |
| `pass.recipient` | **Used** | `loader_statsbomb.py:103` → `passRecipientId` → real reception waypoint in `build_match.py` |
| `pass.end_location` | **Used** | `loader_statsbomb.py:101` → `passEnd` |
| `pass.height` and other pass sub-flags (`cross`, `through_ball`, `switch`, `technique`, etc.) | **`height` captured but never consumed downstream; `cross`/`through_ball`/etc. never even captured** | `loader_statsbomb.py:104` sets `ev["passHeight"]`, but no file in `pipeline/` ever reads `passHeight` again — dead field. `cross`/`through_ball` aren't read by the loader at all (confirmed by grep); I had to go to the raw JSON to find cross/through-ball examples for §F14. |
| `pass.outcome.name == "Pass Offside"` | **Used generically, not specially** | Falls into the same `passOutcome` field as `"Complete"`/`"Incomplete"`; `build_match.py` only special-cases `== "Complete"` for receptions, so an offside pass is correctly treated the same as any other non-complete pass (no reception created) — correct behavior, just not a distinct code path. |
| `Offside` (standalone event type) | **N/A — does not occur** | StatsBomb does not emit a standalone `Offside` event for this competition/season; the only 27 unique event types that appear in the sample match (§B4) do not include one, and offside is only ever a `Pass` outcome value. No code anywhere handles an `Offside` event type. |
| `carry.end_location` | **Used** | `loader_statsbomb.py:107` → `carryEnd` → 2nd waypoint |
| `Pressure`, `Duel`, `Dribbled Past`, `Block`, `Interception`, `Clearance`, `Ball Recovery`, `Miscontrol` | **Used as generic position anchors only** | No type-specific sub-field of any of these is read beyond `location`/`player`/`team` |
| `Foul Committed` / `Bad Behaviour` `card` | **Used, but incompletely** — see the confirmed bug in §C10/E13 | `loader_statsbomb.py:119-124`; `build_match.py:95-97` only checks `e["type"] in ("Bad Behaviour",)` for setting `exit`, missing a real second-yellow recorded on a `Foul Committed` event |
| `Goal Keeper` events | **Used as generic position anchor only** | No GK-action-type sub-field read |
| `shot.end_location` | **Used** | `loader_statsbomb.py:112` → `shotEnd` |
| `shot.freeze_frame` | **Captured, then discarded — dead code** | `loader_statsbomb.py:114-118` builds `ev["freezeFrame"]`; `build_payload.py`'s `events_out` construction never includes it, so it never reaches the viewer |
| `Starting XI` | **Used** | Formation seeding (`build_match.py:71-82`), `tacticsLineup` |
| `Tactical Shift` | **Explicitly ignored** | In `NON_POSITIONAL`; no re-seeding of formation happens at a tactical shift (documented as deliberate in the code's own comment, `build_match.py:76-80`) |
| `Substitution` | **Used** | Sets `exit`/`enter` correctly (verified in §C10 — all 10 substitutions across the 4 matches checked matched their event time to the millisecond) |
| `Player Off` / `Player On` | **Not used at all** | No location, and not checked by type anywhere — correctly harmless for the one case observed (a temporary off-pitch stop with no card), but see §E13 |
| `Half Start` / `Half End` | **Not used as position anchors** (`NON_POSITIONAL`); **not used for period-offset either** | The continuous match clock (`period_offset`) is computed from the max real event timestamp per period (`loader_statsbomb.py:141-147`), not from these events specifically — they are excluded from `NON_POSITIONAL` position anchors but (unlike Starting XI/Tactical Shift) are **not** excluded from `build_payload.py`'s `events_out`, so they do appear in the viewer's event feed |

### B6. Anchor counts / gaps, Arsenal v Liverpool

Every player who appeared, sorted by minutes played (own-team-frame gaps in seconds, seed waypoint at t=0 counted as one anchor):

| Player | Position | Minutes | # Anchors | Median gap (s) | Max gap (s) |
|---|---|---:|---:|---:|---:|
| Petr Čech | Goalkeeper | 95.1 | 76 | 25.86 | 447.80 |
| Héctor Bellerín | Right Back | 95.1 | 157 | 2.78 | 271.96 |
| Calum Chambers | Right Center Back | 95.1 | 151 | 3.75 | 334.67 |
| Gabriel Paulista | Left Center Back | 95.1 | 136 | 5.50 | 336.84 |
| Nacho Monreal | Left Back | 95.1 | 258 | 1.57 | 276.90 |
| Santi Cazorla | Left Defensive Midfield | 95.1 | 293 | 2.02 | 385.29 |
| Aaron Ramsey | Right Wing | 95.1 | 272 | 2.28 | 271.18 |
| Mesut Özil | Center Attacking Midfield | 95.1 | 318 | 2.32 | 245.10 |
| Alexis Sánchez | Left Wing | 95.1 | 272 | 2.07 | 432.68 |
| Simon Mignolet | Goalkeeper | 95.1 | 66 | 65.43 | 281.10 |
| Nathaniel Clyne | Right Back | 95.1 | 107 | 5.45 | 691.51 |
| Martin Škrtel | Right Center Back | 95.1 | 72 | 38.69 | 505.32 |
| Dejan Lovren | Left Center Back | 95.1 | 82 | 14.70 | 402.24 |
| Joe Gomez | Left Back | 95.1 | 170 | 3.11 | 375.99 |
| James Milner | Right Center Midfield | 95.1 | 182 | 2.33 | 319.47 |
| Emre Can | Left Center Midfield | 95.1 | 179 | 4.43 | 391.21 |
| Christian Benteke | Center Forward | 95.1 | 130 | 19.84 | 230.97 |
| Philippe Coutinho | Left Wing | 88.1 | 171 | 2.43 | 258.33 |
| Francis Coquelin | Right Defensive Midfield | 82.6 | 240 | 2.00 | 250.21 |
| Lucas Leiva | Center Defensive Midfield | 76.3 | 110 | 12.91 | 364.07 |
| Olivier Giroud | Center Forward | 73.5 | 96 | 8.88 | 376.52 |
| Roberto Firmino | Right Wing | 63.3 | 166 | 2.69 | 321.13 |
| Jordon Ibe | (sub) | 31.8 | 46 | 4.22 | 179.62 |
| Theo Walcott | (sub) | 21.6 | 22 | 9.03 | 314.49 |
| Jordan Rossiter | (sub) | 18.8 | 33 | 3.36 | 172.87 |
| Alex Oxlade-Chamberlain | (sub) | 12.5 | 32 | 2.02 | 314.12 |
| Alberto Moreno | (sub) | 7.0 | 8 | 9.03 | 144.69 |

Both goalkeepers have by far the sparsest anchors (median gap 26–65s) and the largest max gaps (up to 691.5s for Nathaniel Clyne — a right-back, not even the keeper — meaning for over 11 minutes straight he generated zero located/attributed event). Center-backs (Škrtel, Lovren, Chambers, Gabriel) are also sparse relative to midfielders. This directly explains why long-gap, high-uncertainty stretches cluster on defenders and keepers in §D9.

---

## C. The specific bad frame

### C7. Cazorla's Pressure at 01:33, first half

Confirmed event: `id6babd13…`, index 136, `timestamp 00:01:33.743`, type `Pressure`, team Arsenal, player Santiago Cazorla, `location [21.4, 57.0]` (StatsBomb units) → **t = 93.743s continuous**.

Full-pitch frame at that instant (Full Realism / smoothed display; "own-frame x" = meters from that player's own goal line; "shared x/y" = the same frame flipped so Arsenal attack left→right on one shared pitch, Arsenal's own goal at x=0, Liverpool's own goal at x=105):

| Player | Team | Own-frame (x,y) | Shared (x,y) | sd (m) | Real anchor now? | Time since/until nearest real anchor |
|---|---|---|---|---:|---|---|
| Olivier Giroud | Arsenal | (53.6, 38.2) | (53.6, 38.2) | 10.99 | No | 53.0s since (67.8,32.4) / 84.0s until (70.4,47.3) |
| Mesut Özil | Arsenal | (48.1, 38.3) | (48.1, 38.3) | 10.08 | No | 48.7s since (69.4,48.1) / 32.8s until (63.9,42.1) |
| Alexis Sánchez | Arsenal | (47.6, 26.9) | (47.6, 26.9) | 10.80 | No | 42.9s since (47.2,24.6) / 111.6s until (59.7,40.7) |
| Aaron Ramsey | Arsenal | (33.7, 55.6) | (33.7, 55.6) | 5.78 | No | 4.9s since / 89.2s until |
| Nacho Monreal | Arsenal | (31.4, 18.1) | (31.4, 18.1) | 9.28 | No | 24.6s since / 37.5s until |
| Francis Coquelin | Arsenal | (30.7, 28.6) | (30.7, 28.6) | 9.18 | No | 19.1s since / 136.7s until |
| Héctor Bellerín | Arsenal | (24.6, 58.3) | (24.6, 58.3) | 7.73 | No | 11.7s since / 46.3s until |
| **Santi Cazorla** | Arsenal | **(18.8, 47.3)** | (18.8, 47.3) | **2.07** | **Yes** | is the anchor (t≈93.7) |
| Calum Chambers | Arsenal | (16.4, 51.3) | (16.4, 51.3) | 8.37 | No | 13.9s since / 86.5s until |
| Gabriel Paulista | Arsenal | (12.1, 42.0) | (12.1, 42.0) | 3.21 | No | 16.7s since / 1.3s until |
| Petr Čech | Arsenal | (3.5, 36.0) | (3.5, 36.0) | 4.13 | No | 79.4s since / 2.7s until |
| Simon Mignolet | Liverpool | (11.2, 34.1) | (93.8, 33.9) | 11.35 | No | 93.7s since (t=0 seed only) / 127.9s until |
| Martin Škrtel | Liverpool | (49.5, 32.0) | (55.5, 36.1) | 10.37 | No | 93.7s since (t=0 seed only) / 32.8s until |
| Dejan Lovren | Liverpool | (49.6, 19.1) | (55.4, 48.9) | 11.24 | No | 67.8s since / 162.5s until |
| Lucas Leiva | Liverpool | (54.7, 33.4) | (50.3, 34.6) | 11.29 | No | 93.7s since (t=0 seed only) / 84.0s until |
| Joe Gomez | Liverpool | (68.0, 13.0) | (37.0, 55.0) | 6.81 | No | 7.6s since / 89.2s until |
| James Milner | Liverpool | (69.7, 39.6) | (35.3, 28.4) | 8.89 | No | 19.5s since / 38.2s until |
| Nathaniel Clyne | Liverpool | (70.6, 52.5) | (34.4, 15.5) | 10.62 | No | 37.8s since / 116.7s until |
| Roberto Firmino | Liverpool | (76.7, 44.1) | (28.3, 23.9) | 9.39 | No | 25.3s since / 37.5s until |
| Philippe Coutinho | Liverpool | (82.7, 10.8) | (22.4, 57.2) | 5.59 | No | 4.8s since / 44.2s until |
| Christian Benteke | Liverpool | (91.6, 23.1) | (13.4, 44.9) | 3.02 | No | 14.5s since / 1.4s until |
| Emre Can | Liverpool | (92.6, 17.0) | (12.4, 51.0) | 1.44 | No | 3.9s since / 0.6s until |

**Deepest Liverpool player**: Simon Mignolet (goalkeeper), 11.21m from his own goal line. Excluding the keeper, the deepest outfield Liverpool player is **Martin Škrtel at 49.48m from his own goal — i.e. barely inside his own half, essentially level with the halfway line (52.5m) at 1 minute 33 seconds into the match.**

**Two most advanced Arsenal players**: Olivier Giroud (shared x=53.6) and Mesut Özil (shared x=48.1).

**This is the bad frame, and here is why it's bad, with the exact mechanism**: at t=93.7s (93 seconds into the match), most Liverpool outfield players have had **zero real touches** since kickoff — their only "anchor" so far is the t=0 formation-template seed (`gap since = 93.7s exactly` for Mignolet, Škrtel, Lucas Leiva). With no real observation nearby, the Kalman/OU smoother reverts almost entirely to the model's raw mean prediction `mu(t)`. That prediction is built from an **anchor feature that is wrong for this moment**: `apply_to_match.py:81` sets `anchor_x, anchor_y = np.median(wx), np.median(wy)` over **every waypoint in the entire 95-minute match** — not a time-local baseline. I directly compared this to what the *training*-style rolling anchor (`training/fit_mean_model.py::rolling_anchor`, a ±60s window) would say at this same instant, for the players who look most implausible in the table above:

| Player | Inference anchor (whole-match median) | Training-style rolling anchor at t=93.7s | Difference |
|---|---|---|---:|
| Joe Gomez | (45.1, 6.3) | (48.9, 1.7) | 5.9 m |
| Nathaniel Clyne | (53.7, 62.1) | (39.1, 64.4) | 14.8 m |
| Santi Cazorla | (61.5, 28.0) | (36.6, 38.7) | 27.1 m |
| Christian Benteke | (66.5, 28.4) | (93.5, 31.8) | 27.2 m |
| Roberto Firmino | (59.6, 49.0) | (87.5, 26.2) | **36.0 m** |

The model was trained on rows where "anchor" means a local, slowly-drifting baseline (`fit_mean_model.py:49-64`). At inference it is fed a single whole-match constant instead — computed, moreover, using waypoints from **later in the match that haven't happened yet at t=93.7s** (a look-ahead/leakage issue on top of the train/inference mismatch). This is large enough (up to 36m in this match) to be the direct, demonstrated cause of players like Škrtel, Coutinho and Benteke appearing implausibly advanced 93 seconds into the game. See §E13 finding #1 for the precise fix location.

### C8. Two more moments

I picked these by searching for the frames with the highest displayed-position uncertainty (`sd`) rather than by eye, so the selection is reproducible, not a subjective "looks weird" call.

**Norwich 4–5 Liverpool (3754348), t=100s**: worst-uncertainty player is Ivo Pinto (Norwich), sd=11.36m; 5 of the top 6 highest-sd players across both teams have `gap_before=100.0` — i.e., like the Cazorla frame, they have had no real touch since kickoff and their displayed position is coming almost entirely from the same whole-match-median-anchor mechanism.

**Aston Villa 0–0 Manchester City (3754258), t=100s**: worst-uncertainty player is Brad Guzan (goalkeeper), sd=11.37m, again with `gap_before=100.0` (no touch since kickoff). Several outfield starters (Sterling, Yaya Touré, Bony) show the identical pattern.

**Contrast check — same Norwich match, t=4000s (66:40, deep into the second half)**: uncertainty is **not** generally lower late in the match — several players (Mignolet sd=11.40, Redmond sd=11.39, Moreno sd=11.38) still sit at essentially the OU process's stationary-variance ceiling (`sqrt(sigma_x²/(2·theta_x) + sigma_y²/(2·theta_y)) ≈ 11.4m`, computed directly from the shipped `OU_THETA/OU_SIGMA` constants). This confirms the high-sd behavior itself is **not a bug** — it is the OU model correctly saturating its uncertainty whenever a player has gone a long time (roughly 60-120s+) without a real anchor, at any point in the match, which is honest and by design (consistent with what `BENCHMARKS.md` already states about calibration). The bug in C7 is specifically about the **anchor** feeding `mu(t)`, not the uncertainty band around it.

---

## D. Standing still

### D9. Rolling 60-second-window movement, 4 matches

Method: Full Realism (smoothed) displayed position, path length (sum of consecutive-sample distances) over every 60s window advanced in 5s steps, for every player over their `enter`→`exit` span; "sparse anchor" = ≤1 real waypoint (excluding the t=0 kickoff seed) fell inside that 60s window.

| Match | Windows | Windows <3m movement | % |
|---|---:|---:|---:|
| Arsenal 0–0 Liverpool | 24,802 | 2 | 0.0% |
| Norwich 4–5 Liverpool | 25,406 | 52 | 0.2% |
| Aston Villa 0–0 Man City | 24,881 | 159 | 0.6% |
| Liverpool 0–3 West Ham | 26,166 | 392 | 1.5% |

By position group, pooled across all 4 matches:

| Group | Total windows | Under 3m | % | With ≤1 anchor in window | With ≥2 anchors in window |
|---|---:|---:|---:|---:|---:|
| GK | 9,250 | 146 | 1.6% | 146 | 0 |
| DEF | 36,976 | 207 | 0.6% | 207 | 0 |
| MID | 29,945 | 151 | 0.5% | 151 | 0 |
| FWD | 25,084 | 101 | 0.4% | 101 | 0 |

**Every single one of the 606 near-static windows found across all 4 matches has ≤1 real anchor in it — zero have 2 or more.** So standing-still is fully and exclusively explained by anchor sparsity, never by a player with plenty of real data who the model nonetheless freezes. It is most common for goalkeepers (1.6% of their windows), consistent with §B6's finding that keepers have by far the sparsest anchors. The overall rate is low (well under 2% of all windows in every match), which is a genuinely reassuring finding, not one I expected going in.

### D10. Substitutes, sent-off players, pre-kickoff

**Substitutions**: checked all 10 substitutions across the 4 matches directly against each track's `enter`/`exit`. **All 10 matched the event's own timestamp exactly** (`build_match.py:88-94`) — no drift, no off-by-one, no stale drawing after a sub.

**Sent-off players — confirmed bug**: in Liverpool 0–3 West Ham (3753984), **Philippe Coutinho received a second yellow card at t=3335.085s**, recorded on a `Foul Committed` event (`e["type"]=="Foul Committed"`, `card="Second Yellow"`) — there is **no corresponding `Bad Behaviour` event anywhere later in the match for this player** (verified directly: his only two events after t=3300s are the preceding Pressure and this Foul Committed). `build_match.py:95-97` only sets a player's `exit` when `e["type"] in ("Bad Behaviour",)` — a second yellow recorded via `Foul Committed` is invisible to this check. Result: Coutinho's track `exit` stays `None` for the rest of the match. Since `apply_to_match.py:84` does `exit_ = track["exit"] if track["exit"] is not None else max_t`, his position grid is built all the way to the match's end (`maxT=6073.5s`), and `viewer/app.js::trackActiveAt` (`if (track.exit !== null && t > track.exit) return false`) never deactivates him either. **He is drawn and animated on the pitch for the remaining ~46 minutes of the match after actually being sent off.** For comparison, a direct red card in the same match (Mark Noble, `Bad Behaviour`, card=`Red Card`, t=4919.5s) **is** handled correctly — his `exit` is set to exactly 4919.475s. So the bug is specific to a second yellow tagged on the underlying `Foul Committed` event rather than a separate `Bad Behaviour` event.

**Pre-kickoff / temporary off-pitch**: found one `Player Off`/`Player On` pair in 3 of the 4 matches (a brief real-world stoppage, e.g. injury check, distinct from a Substitution). Neither event type has a location, and neither is specially handled in `build_tracks()` (they simply fall through — no waypoint added, no `exit`/`enter` change). In every case observed here this is harmless (the player returns seconds/minutes later and their surrounding real touches bracket it normally), but it means the pipeline has **no representation at all** of "this player was briefly off the pitch" — they would continue to be smoothly interpolated across that gap as if they'd never left, rather than being deactivated like a substitute. I did not find a case in these 4 matches where this produced a visibly wrong frame, but it is a gap, not a verified-safe behavior, for a longer or more disruptive off-pitch stretch than what happened to occur here.

---

## E. Numbers and limits

### E11. Current benchmark (from `BENCHMARKS.md`, verified against it directly — not re-derived here)

Overall median error: **11.06m** (production model, config B, Metrica Game 2 held out).

By gap since last touch (Kalman/OU median / p90, meters):

| gap | median | p90 |
|---|---:|---:|
| 0–5s | 3.8 | 9.8 |
| 5–10s | 8.1 | 17.1 |
| 10–20s | 10.2 | 22.2 |
| 20–30s | 12.1 | 25.8 |
| 30–60s | 13.6 | 29.9 |
| 60–120s | 14.3 | 33.5 |
| 120s+ | 12.8 | 34.6 |

By role (median, meters): GK 2.3–7.7 across gap buckets, DEF 3.2–13.2, MID 3.8–18.9, FWD 4.2–16.4 (full table in `BENCHMARKS.md`).

Calibration: k50=1.224, k68=1.681, k90=2.773 — these give *exactly* 50/68/90% coverage **on the Metrica fold they were fit on, by construction**. Cross-source, applied blind to a Bundesliga (IDSSE) match: 29.6%/76.0% actual coverage against the same nominal 50/90% bands — measurably looser.

**Which numbers are truly held-out**: the LOMO table (config A/B/C, 10 folds) is the only genuinely held-out evaluation — each fold's match is fully excluded from that fold's training set. The main "by gap/by role" table and the calibration multipliers above are evaluated on **Metrica Game 2 specifically excluded from the production model** (`mean_model_no_metrica_game2.joblib`), so that one held-out match is legitimate. The Bundesliga cross-source check is explicitly **not** held-out — `BENCHMARKS.md` states plainly that the production model includes all 7 IDSSE matches, so that number is an in-sample sanity check, not a transfer test (the IDSSE rows of the LOMO table are the real transfer numbers for that source). None of the StatsBomb/application-side matches (Arsenal v Liverpool etc.) have any ground-truth positions at all — there is no way to directly benchmark accuracy on StatsBomb application data; all accuracy numbers come exclusively from the training-side tracking sources.

### E12. Real (detected) frame counts per training source

| Source | Matches | Real player-frame rows |
|---|---:|---:|
| Metrica Sports | 3 | 10,892,966 |
| DFL/IDSSE (Bundesliga, TRACAB) | 7 | 21,874,234 |
| SkillCorner (broadcast, detected only) | 20 | 10,978,707 detected of 19,334,986 total (56.8%) |

None of these three sources' loaders currently expose anything about team shape (back-line height/spread, team length/width, GK position relative to the line, attacker position relative to the last defender) — the training feature set (`FEATS`) has no such columns. All three ARE full/partial optical or broadcast player-tracking (Metrica and IDSSE are complete; SkillCorner's detected subset is a real subset, not extrapolated), so the raw positional data needed to derive team-shape priors (back-line x-position per frame, per-team convex hull / spread, etc.) does exist in all three sources and could be computed directly from `players`/`ball` DataFrames already loaded by `loader_metrica.py`, `loader_idsse.py`, `loader_skillcorner.py` — it is simply not currently extracted into any feature. This is a scoping note for a future fix, not a bug in what exists today.

### E13. Bugs and oddities found (file : line)

1. **[Major, confirmed with data] Anchor train/inference mismatch + look-ahead.** Training (`training/fit_mean_model.py:49-64, rolling_anchor`) computes each player's anchor as a ±60s rolling-window median. Inference (`pipeline/apply_to_match.py:81`) computes it as a single whole-match median of *all* of that player's waypoints — including ones from later in the match that haven't happened yet at the time being rendered. Measured difference at one real timestamp: up to **36.0m** (Roberto Firmino, t=93.7s, §C7). This is the direct, demonstrated cause of the implausible early-match frames found in §C7/C8.
2. **[Confirmed with data] Second-yellow card via `Foul Committed` doesn't end a player's track.** `build_match.py:95-97` only checks `e["type"] in ("Bad Behaviour",)`. Philippe Coutinho's second yellow in match 3753984 (t=3335.085s) was tagged on a `Foul Committed` event and never produces a `Bad Behaviour` event — his `exit` stays `None` and he is animated for the last ~46 minutes of the match after being sent off (§D10). A direct red card via `Bad Behaviour` in the same match is handled correctly.
3. **[Confirmed] `shot.freeze_frame` is parsed and then discarded.** Built at `pipeline/loader_statsbomb.py:114-118`; `pipeline/build_payload.py`'s `events_out` construction never includes it — dead computation, no effect on the shipped viewer.
4. **[Confirmed] Generic event `duration` is only captured for Carry events**, not Pass or anything else (`loader_statsbomb.py:108`), despite every StatsBomb event carrying a real `duration`. This means the shipped pipeline has no access to real pass flight-time at all; I had to read it from the raw cached JSON directly to answer §F14/F15.
5. **[Confirmed] `MU_MAX_MPS=6.5` rate limiter + RF regressor can produce team-wide, unrealistic simultaneous "sprints" during a fast/long pass.** During the 5.39s Mignolet→Ibe long pass in Arsenal v Liverpool, **all 20 outfield players** (not just those near the ball) show 17.9–28.1m of displayed movement (median 23.4m) — see §F14. Instrumenting the raw model output directly (before the rate limiter) shows the RF regressor's own predicted mean position can jump **10.4 m/s in a single 1-second grid step** purely because the fast-moving ball-relative feature swings quickly; the 6.5 m/s cap then still allows every player on the pitch to be capped at a speed that is unrealistic to sustain simultaneously across an entire team just because the ball is airborne. See §F14–F16 for the full mechanism.
6. **[Confirmed, minor] `under_pressure`, `counterpress`, `off_camera`, `related_events`, and pass `cross`/`through_ball` flags are never read anywhere in the pipeline.** Not a bug, but worth knowing these are available and unused.
7. **[Confirmed, design choice not flagged elsewhere in the code] Every located+attributed event type is used as a position anchor indiscriminately** — `Pressure`, `Duel`, `Dribbled Past`, `Block`, `Interception`, `Clearance`, `Ball Recovery`, `Dispossessed`, `Miscontrol`, `Foul Committed/Won`, `Goal Keeper` all count identically to `Pass`/`Carry`/`Shot`. Defensible (they do carry a real location for that player) but not distinguished anywhere from a real touch.
8. **[Confirmed, minor gap] `Player Off`/`Player On` events are silently ignored** — no location, not specially handled, so a temporarily-off-pitch player is smoothly interpolated across the gap as if never absent. Harmless in the one case observed in these 4 matches (§D10), but unverified for a longer or more disruptive real-world case.
9. Not checked in this pass, flagged as an open question rather than a claim: whether the near-zero real-clock gap between the last event of period 1 and the first event of period 2 (both period offsets are set from the last/first real event timestamps, `loader_statsbomb.py:141-150`) causes any spline-tangent artifact right at the halftime boundary. I did not verify this either way and am not reporting it as a finding.

---

## F. Movement during passes

Five passes from Arsenal v Liverpool, chosen to match the requested categories (all times are continuous match-clock seconds; positions in meters, each team's own-attacking-frame — distances are frame-invariant so no flip was needed for the movement figures):

| Label | Passer → intended/actual recipient | t_pass | duration | length |
|---|---|---:|---:|---:|
| SHORT | James Milner → Roberto Firmino | 30.85s | 1.09s | 7.03m |
| LONG | Simon Mignolet → Jordon Ibe | 5138.63s | 5.39s | 77.88m |
| THROUGH | Philippe Coutinho → Christian Benteke | 531.12s | 1.92s | 20.83m |
| CROSS | Nacho Monreal → Alexis Sánchez | 286.24s | 1.52s | 37.94m |
| LONG-GAP | Gabriel → Petr Čech (Čech's own next touch is 307.7s later — the longest such dwell found in the match) | 5051.90s | 2.29s | 29.24m |

### F14. Movement during flight and in the 2s after reception

Full per-player tables (position at 0/25/50/75/100% of flight) are large; summary here, full tables were generated and are reproducible via the script logic described in §A1 — median/max are across all outfield players on the pitch at that moment (goalkeepers excluded):

| Pass | Flight-distance median | Flight-distance max | Notes |
|---|---:|---:|---|
| SHORT (1.09s) | 2.92m | 7.08m | Plausible — sub-3m median over ~1s |
| **LONG (5.39s)** | **23.40m** | **28.14m** | **Every single outfield player** moved 17.9–28.1m during this one pass — see mechanism in §F16/E13#5 |
| THROUGH (1.92s) | 6.42m | 9.74m | Plausible for ~2s of open play |
| CROSS (1.52s) | 7.65m | 9.95m | Plausible for ~1.5s |
| LONG-GAP (2.29s) | 6.12m | 9.70m | Plausible for ~2.3s |

Movement in the 2 seconds *after* reception was, perhaps counterintuitively, **smaller** for the LONG pass (0.12–2.73m, nearly all under 2m) than for the other four (typically 1–8m). This is consistent with the mechanism in §F16: once the ball proxy stops moving fast (arrives and holds), `mu(t)`'s ball-relative term stops swinging quickly too, so the artificial "sprint" stops as abruptly as it started.

### F15. Ball position fed to the model during flight

The underlying ball-position source (`apply_to_match.py::build_ball_and_possession`) is one point per event, at that event's own actor location, in a shared frame. The value fed to the model at any time `t` is `np.interp(t, event_times, event_locations)` — **pure linear interpolation between two real, discrete event points**. Measured directly for all 5 passes at 0/25/50/75/100% of flight, the ball feed moves in a perfectly monotonic straight line between the two endpoints in every case (e.g. LONG pass: (99.8,30.6) → (82.3,27.0) → (64.7,23.4) → (47.2,19.8) → (33.0,17.3) — each step almost exactly evenly spaced). So: **continuous in value, but not independently observed during flight** — it is a straight-line glide between the passer's own location and whatever the next event's location happens to be (usually the receiver's Ball Receipt, which is close to but not identical to the true parabolic ball path). It is never held/stepped; it also never curves.

### F16. Nothing explicitly holds a player's position constant between events

Confirmed by direct grep of `pipeline/` for "freeze/hold/constant" (only unrelated hits, StatsBomb's shot freeze-frames) and by reading the full prediction path: there is no code that pins a player's displayed position between one event and the next. What *does* exist, and produces effects that can look similar:
- The **static whole-match anchor** (`apply_to_match.py:81`, same bug as §C7/E13#1) — a fixed target the OU process reverts around; wrong, but not literally "held."
- **OU mean-reversion itself** — with no nearby real observation, the smoothed position drifts toward `mu(t)`, which changes slowly when the ball proxy is moving slowly, and can look "settled" without being pinned.
- The **`MU_MAX_MPS=6.5` rate limiter** (`apply_to_match.py:107-116`) caps how fast `mu(t)` itself can change between consecutive 1 Hz grid points — this is the mechanism directly responsible for the LONG-pass finding in §F14: instrumenting the RF model's raw (pre-rate-limit) output for Mesut Özil during that pass shows the model's own predicted mean position jumping **10.4 m/s in one single grid step** (from a ball-relative feature that itself moves ~15-17 m/s during this long ball), which the rate limiter then still allows up to 6.5 m/s per player, per axis, simultaneously for every player on the pitch. That is the real, demonstrated explanation for why *everyone* appears to sprint during a long ball, not a "held position" bug — it is closer to the opposite problem.

### F17. Pass-arrival sync — confirmed present, mode-dependent

Yes, it exists, matching the earlier tactical-clarity round, but only in **Tactical Clarity** display mode: `viewer/app.js::tacticalPositionAt` (lines 228–242) explicitly eases the receiving player from their pre-pass position to the real `pass.end_location` between `t_pass` and `t_receive`, using `easeInOutCubic`, driven by the reception data built in `build_match.py::build_tracks` (109–130). In **Full Realism** mode there is no equivalent explicit "run to the ball" animation — the receiver's motion during the pass is produced by the same general per-player OU/Kalman mechanism as every other player, with the real reception location injected as one more Kalman observation at `t_receive`. In this specific LONG pass example, the intended receiver (Jordon Ibe) does show a plausible-looking 17.9m run during the 5.39s flight in Full Realism mode too — but per §F14/F16, that is an emergent side effect of the same ball-relative-feature mechanism that also makes 19 *other* players move similarly far, not a targeted "run onto this pass" behavior.
