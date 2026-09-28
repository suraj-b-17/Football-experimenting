# Status (one line per task; newest last)

- T0 started: Node harness `tests/viewer_harness.js` + `tests/drivers/frame_dump.js` written; loader edit (duration/flags for all events) started ahead of T1 — does not affect T0, which reads the already-built payloads.
- T0 done: default=Full Realism; screenshot=Tactical; Tactical freezes (12-18/20 players <1m during flights), Realism sprints (LONG median 23.7m); Tactical static windows GK 48.5% DEF 12.2% MID 7.6% FWD 10.4%. Results in CHANGELOG_fix.md + tests/baseline/.
- T5b diagnosis done (before state): ball ignores carries; Tactical reception windows snap + spline overshoot + t=0 seed; Realism misses 66-69% of anchors by >0.5m (1 s grid). See CHANGELOG_fix.md. T1 build_match rewritten (cards any event, Player Off/On intervals, duration all events, freeze anchors, kickoffs); tracking prep running.
- T1d kickoff templates fitted from 27 real kickoffs (591 player positions) -> models/kickoff_template.json; T2 LOMO running in background.
- T2: mean smoothing sigma=2 s chosen by held-out error (9.83 vs 10.17 m), removes >9.5 m/s steps; wired into train_v2 + apply_to_match.
- T5b/T6/T7 on 3754129 (provisional base_B bundle): QA clean both modes (0 anchor misses, 0 non-anomaly fast runs, carries <=9.5). Waiting on full LOMO.
- T4 counts done (tests/analysis/field_counts.py); D9 collision fix (no averaging). LOMO still running.
- T2 LOMO (unsmoothed) done: stage2_B best 9.89 m vs OLD 11.13 m (10/10 folds); SkillCorner no gain; team shape compressed. Re-running B with 2 s smoothing.
- T2/T3 final: stage2_B shipped (LOMO 9.58 m vs OLD 11.13 m, 10/10 folds). Shape stretch 1.2 matches real shape but worsens error 9.68->9.91 -> display-only toggle.
- T7 evidence (after) captured in tests/evidence/ for 4 matches; season rebuild in progress.
- Season rebuilt: see output/run_season.log. Running QA on 380.
- T6b: file:// works (script-tag payloads, local CSS, no CDN); Chrome offline + Firefox 5/5 pass, moved copy passes; payloads 1543->1281 MB, max change 0.0500 m; static QA check added. Full QA running.
- T7: boundary fixes (interval rounding, anchor tolerance, QA conventions); final season rebuild started.
- D13/D14/D15 documented; final 380-match rebuild+QA running.
- Final season QA: max 16.73 m/s (was 234), p99 14.3, median 11.9, 0 anchor misses/1.11M, 0 active-count mismatches. Residual >12m/s (kickoff soft-anchor Kalman transitions) flagged, not yet fixed.
- Collision distance audit: true-duplicate rule verified correct (0 violations of 580,633); 91.55m case is a drop (John Stones, 3754163), not a merge. No code change needed.
