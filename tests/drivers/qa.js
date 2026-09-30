// Automated per-match QA on the real viewer, both modes, 60 fps.
// Prints one JSON object: {matchId, <mode>_* metrics, lists of flagged items}.
const FPSq = 60, DTq = 1 / FPSq;
const VMAX = 9.5 + 0.05;  // tolerance for float rounding at the cap
const anomalies = MATCH_DATA.anomalies || [];
// The payload now logs EVERY collision, including true same-instant duplicates
// (D12/D9) — season-wide that's ~1500 entries per match, so a per-frame,
// per-player, un-indexed scan of the full array (as this used to be) is
// O(matchDuration * 60 * players * 2modes * anomalies) and took ~4 min/match.
// Index by player id once; only 'collision_retimed'/'collision_dropped' (not
// duplicates, which explain nothing about a speed spike) and 'carry_speed'
// ever excuse a fast frame.
const anomPairs = anomalies.filter(a => a.type === 'anchor_pair_speed');
const collisionAnoms = anomalies.filter(a => a.type === 'collision_retimed' || a.type === 'collision_dropped');
const byPlayer = new Map();
for (const a of anomalies) {
  if (a.type !== 'collision_retimed' && a.type !== 'collision_dropped' &&
      a.type !== 'anchor_pair_speed' && a.type !== 'carry_speed') continue;
  if (!byPlayer.has(a.playerId)) byPlayer.set(a.playerId, []);
  byPlayer.get(a.playerId).push(a);
}
function nearAnomaly(pid, t) {
  const list = byPlayer.get(pid);
  if (!list) return false;
  for (const a of list) {
    if (a.type === 'collision_retimed' || a.type === 'collision_dropped') {
      if (t >= a.t - 0.6 && t <= (a.t_new || a.t) + 0.6) return true;
    } else if (a.type === 'anchor_pair_speed') {
      if (t >= a.t - 0.6 && t <= a.t + a.dt_s + 0.6) return true;
    } else if (t >= a.t - 0.1 && t <= a.t + a.len_m / 9.5 + 0.7) return true;
  }
  return false;
}
const tracks = [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks];
// period boundaries are a deliberate cut (half-time), not movement
const cuts = (MATCH_DATA.periods || []).slice(1).map(p => p.start);
const crossesCut = (t0, t1) => cuts.some(c => t0 < c + 1e-6 && t1 >= c - 1e-6);
const res = { matchId: MATCH_ID };
for (const mode of ['realism', 'tactical']) {
  displayMode = mode;
  let fastRunsModel = 0, fastRunsImplied = 0;
  const worstFrames = [];
  let fastRuns = 0, fastRunsAnom = 0, outFrames = 0, maxOut = 0, maxSpeed = 0, maxSpeedNonAnom = 0;
  const flagged = [];
  for (const tr of tracks) {
    for (const [a, b] of tr.intervals) {
      let prev = null, run = 0, runStart = 0, runAnom = false;
      for (let t = a; t <= b; t += DTq) {
        const p = playerPositionAt(tr, t);
        const out = Math.max(-2 - p[0], p[0] - (PITCH_LEN_M + 2), -2 - p[1], p[1] - (PITCH_WID_M + 2));
        if (out > 0.01) { outFrames++; maxOut = Math.max(maxOut, out); }
        if (prev && crossesCut(t - DTq, t)) { prev = p; continue; }
        if (prev) {
          const v = Math.hypot(p[0] - prev[0], p[1] - prev[1]) / DTq;
          const anom = nearAnomaly(tr.id, t);
          if (!anom && v > 9.5) worstFrames.push([v * DTq, tr, t]);
          maxSpeed = Math.max(maxSpeed, v); if (!anom) maxSpeedNonAnom = Math.max(maxSpeedNonAnom, v);
          if (v > VMAX) { if (run === 0) { runStart = t; runAnom = false; } run++; runAnom = runAnom || anom; }
          else {
            if (run * DTq > 1.0) {
              if (runAnom) fastRunsAnom++;
              else {
                // classify: do the real anchors bracketing the run themselves imply a sprint?
                const A = tr.anchors.filter(an => an[3] !== 4);
                const lo = A.filter(an => an[0] <= runStart).pop(), hi = A.find(an => an[0] >= t);
                const implied = lo && hi ? Math.hypot(hi[1] - lo[1], hi[2] - lo[2]) / Math.max(hi[0] - lo[0], 1e-3) : 0;
                const cls = implied >= 7.5 ? 'sprint_implied_by_real_events' : 'model_driven';
                if (cls === 'model_driven') fastRunsModel++; else fastRunsImplied++;
                fastRuns++;
                flagged.push({ mode, name: tr.name, t: +runStart.toFixed(2), dur_s: +(run * DTq).toFixed(2), implied_mps: +implied.toFixed(1), cls });
              }
            }
            run = 0;
          }
        }
        prev = p;
      }
    }
  }
  // worst single frames outside logged anomalies (half-time cut excluded above), with context
  worstFrames.sort((a, b) => b[0] - a[0]);
  const kicks = (MATCH_DATA.kickoffs || []).map(k => k[0]);
  const worstList = worstFrames.slice(0, 5).map(([d, tr, t]) => {
    const near = (x, w) => Math.abs(x - t) <= w;
    const ctx = {
      anchors: tr.anchors.filter(a => near(a[0], 1)).map(a => [+a[0].toFixed(3), a[1], a[2], a[3]]),
      collisions: collisionAnoms.filter(a => a.playerId === tr.id && near(a.t, 2)),
      reentry: tr.intervals.filter(([a, b]) => near(a, 2) || near(b, 2)),
      kickoff: kicks.filter(k => near(k, 3)).length > 0,
      carry: tr.carryObjs.some(c => t >= c.t0 - 0.05 && t <= c.tEnd + 0.55),
      reception: (tr.recObjs || []).some(r => t >= r.t_pass && t <= r.t_receive),
    };
    let cause = 'reconstruction between anchors';
    const A = tr.anchors.filter(a => a[3] !== 4), lo = A.filter(a => a[0] <= t).pop(), hi = A.find(a => a[0] >= t);
    const implied = lo && hi ? Math.hypot(hi[1] - lo[1], hi[2] - lo[2]) / Math.max(hi[0] - lo[0], 1e-3) : 0;
    if (ctx.kickoff) cause = 'kickoff soft anchor';
    else if (ctx.reentry.length) cause = 'interval start/end (re-entry)';
    else if (ctx.carry) cause = 'carry path';
    else if (mode === 'tactical' && ctx.reception) cause = 'pass anticipation';
    else if (implied > 9.5) cause = 'bracketing real anchors imply it (' + implied.toFixed(1) + ' m/s)';
    return { mode, name: tr.name, t: +t.toFixed(3), dist_m: +d.toFixed(3), speed_mps: +(d / DTq).toFixed(1), cause, implied_mps: +implied.toFixed(1), ...ctx };
  });
  res[mode + '_worst_frames'] = worstList;
  // anchor exactness at each hard anchor's own timestamp (off-camera anchors are deliberately soft)
  let nAnch = 0, miss = 0, missAnom = 0, worst = 0;
  const misses = [];
  for (const tr of tracks) for (const an of tr.anchors) {
    if (an[3] === 4 || !trackActiveAt(tr, an[0])) continue;
    nAnch++;
    const p = playerPositionAt(tr, an[0]);
    const d = Math.hypot(p[0] - an[1], p[1] - an[2]);
    if (d > 0.5) {
      if (nearAnomaly(tr.id, an[0])) missAnom++; else { miss++; worst = Math.max(worst, d); if (misses.length < 20) misses.push({ mode, name: tr.name, t: an[0], kind: an[3], err_m: +d.toFixed(2) }); }
    }
  }
  // carries: max displayed carrier speed during non-anomalous carries
  let carryMax = 0, carryFramesOver = 0, carriesOver = 0, nCarries = 0;
  for (const tr of tracks) for (const c of tr.carryObjs) {
    if (c.anom) continue;
    nCarries++;
    let prev = null, over = 0;
    for (let t = c.t0; t <= c.t1 + 1e-9; t += DTq) {
      const p = playerPositionAt(tr, t);
      if (prev) { const v = Math.hypot(p[0] - prev[0], p[1] - prev[1]) / DTq; carryMax = Math.max(carryMax, v); if (v > VMAX) over++; }
      prev = p;
    }
    carryFramesOver += over; if (over) carriesOver++;
  }
  Object.assign(res, {
    [mode + '_fast_runs_gt1s']: fastRuns, [mode + '_fast_runs_gt1s_data_anomaly']: fastRunsAnom,
    [mode + '_fast_runs_implied_by_real_events']: fastRunsImplied, [mode + '_fast_runs_model_driven']: fastRunsModel,
    [mode + '_max_speed_mps']: +maxSpeed.toFixed(2), [mode + '_max_speed_non_anomaly_mps']: +maxSpeedNonAnom.toFixed(2),
    [mode + '_frames_outside_pitch_gt2m']: outFrames, [mode + '_max_outside_m']: +maxOut.toFixed(2),
    [mode + '_anchors_checked']: nAnch, [mode + '_anchor_misses_gt0_5m']: miss, [mode + '_anchor_misses_data_anomaly']: missAnom,
    [mode + '_anchor_worst_miss_m']: +worst.toFixed(2),
    [mode + '_carries_checked']: nCarries, [mode + '_carry_max_speed_mps']: +carryMax.toFixed(2),
    [mode + '_carries_with_frames_gt9_5']: carriesOver, [mode + '_carry_frames_gt9_5']: carryFramesOver,
    [mode + '_flagged']: flagged.concat(misses),
  });
}
// On-screen checks under pacing (what a viewer actually sees at 1x, 60 fps):
// speeds per second of PLAYBACK, stoppage skips excluded (dimmed, labelled
// fast-forward), period cuts excluded. Caps +1 % for float rounding.
for (const mode of ['realism', 'tactical']) {
  displayMode = mode;
  const cutsP = (MATCH_DATA.periods || []).slice(1).map(p => p.start);
  let t = 0, frames = 0, pOver = 0, bOver = 0, pMax = 0, bMax = 0, pFrames = 0, skipPb = 0, skipMatch = 0;
  const pv = [];
  let prev = null, prevB = null;
  while (t < maxT) {
    const t1 = paceAdvance(t, DTq);
    const skipping = inSkip(t) || inSkip(t1), cut = cutsP.some(c => t < c && t1 >= c);
    if (skipping) { skipPb += DTq; skipMatch += t1 - t; prev = null; prevB = null; t = t1; continue; }
    const fr = framePositions(t1, false), b = ballStateAt(t1);
    const cur = new Map(fr.map(f => [f.tr, f]));
    if (prev && !cut) {
      for (const f of fr) {
        const q = prev.get(f.tr); if (!q) continue;
        const v = Math.hypot(f.x - q.x, f.y - q.y) / DTq, cap = Math.max(f.own, q.own) > 0.5 ? 7.5 : 8.0;
        pFrames++; pMax = Math.max(pMax, v); if (v > cap * 1.01) pOver++;
        if ((frames & 7) === 0) pv.push(v);
      }
      const vb = Math.hypot(b.x - prevB.x, b.y - prevB.y) / DTq;
      bMax = Math.max(bMax, vb); if (vb > 30 * 1.01) bOver++;
    }
    prev = cur; prevB = b; t = t1; frames++;
  }
  pv.sort((a, b) => a - b);
  // ball continuity at every segment boundary of the ball model
  let disc = 0;
  for (const g of BM.G) { const a = ballModelAt(g.ta - 1e-4), c = ballModelAt(g.ta + 1e-4); disc = Math.max(disc, Math.hypot(a.x - c.x, a.y - c.y)); }
  const P = paceProfile(mode);
  let stretched = 0, compressed = 0;
  for (let i = 0; i < P.S.length && i * PACE_BIN_S < maxT; i++) { if (P.S[i] > 1.001) stretched += (P.S[i] - 1) * PACE_BIN_S; else if (P.S[i] < 0.999) compressed += (1 - P.S[i]) * PACE_BIN_S; }
  Object.assign(res, {
    [mode + '_onscreen_player_max_mps']: +pMax.toFixed(2), [mode + '_onscreen_player_p999_mps']: +pv[Math.floor(pv.length * 0.999)].toFixed(2),
    [mode + '_onscreen_player_frames_over_cap']: pOver, [mode + '_onscreen_player_frames']: pFrames,
    [mode + '_onscreen_ball_max_mps']: +bMax.toFixed(1), [mode + '_onscreen_ball_frames_over_30']: bOver,
    [mode + '_ball_discontinuity_max_m']: +disc.toFixed(4),
    [mode + '_playback_1x_s']: +(frames * DTq + skipPb).toFixed(1), [mode + '_match_s']: +maxT.toFixed(1),
    [mode + '_skips']: SKIPS.length, [mode + '_skip_match_s']: +skipMatch.toFixed(1), [mode + '_skip_playback_s']: +skipPb.toFixed(1),
    [mode + '_stretch_added_s']: +stretched.toFixed(1), [mode + '_compress_saved_s']: +compressed.toFixed(1),
    [mode + '_pace_max_S']: +Math.max(...P.S.slice(0, Math.ceil(maxT / PACE_BIN_S))).toFixed(2), [mode + '_pace_bins_at_stretch_max']: P.capped,
  });
}
Object.assign(res, { ball_model: ballStats, stoppages: STOPS.length });
// active player counts vs substitutions / cards / player off, sampled every 10 s
const sorted = timeline.map(i => events[i]);
let countErrors = 0, maxActive = 0;
const countFlags = [];
for (const side of ['home', 'away']) {
  const teamId = MATCH_DATA[side].id;
  for (let t = 5; t < maxT; t += 10) {
    let expected = 11;
    const offNow = new Set();
    for (const e of sorted) {
      if (e.t >= t) break;   // an exit at exactly t still counts the player at t (intervals are inclusive)
      if (e.teamId !== teamId) continue;
      if (e.card === 'Red Card' || e.card === 'Second Yellow') expected--;
      if (e.type === 'Player Off') offNow.add(e.playerId);
      if (e.type === 'Player On' || e.type === 'Substitution') offNow.delete(e.playerId);
    }
    expected -= offNow.size;
    const active = MATCH_DATA[side].tracks.filter(tr => trackActiveAt(tr, t)).length;
    maxActive = Math.max(maxActive, active);
    if (active !== expected) { countErrors++; if (countFlags.length < 10) countFlags.push({ side, t, active, expected }); }
  }
}
// collisions: anchor times must be strictly increasing per track
let collisions = 0;
for (const tr of tracks) for (let i = 1; i < tr.anchors.length; i++) if (tr.anchors[i][0] <= tr.anchors[i - 1][0]) collisions++;
Object.assign(res, { active_count_mismatches: countErrors, max_active_per_team: maxActive, count_flags: countFlags,
  anchor_time_collisions: collisions,
  collisions_retimed: anomalies.filter(a => a.type === 'collision_retimed').length,
  collisions_dropped: anomalies.filter(a => a.type === 'collision_dropped').length,
  data_anomalies_carry: anomalies.filter(a => a.type === 'carry_speed').length,
  data_anomalies_anchor_pair: anomPairs.length });
console.log(JSON.stringify(res));
