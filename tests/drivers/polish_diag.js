// Polish diagnosis on the viewer as displayed: player and ball on-screen speed
// at 60 fps of PLAYBACK time at 1x, and ball "still, then teleport" cases.
// Works on the pre-polish viewer (match time == playback time at 1x) and on the
// paced viewer (uses paceAdvance() when it exists).
// Usage: APP_JS=... DATA_DIR=... node tests/viewer_harness.js <id> tests/drivers/polish_diag.js
const RUN_CAP_D = 8.0, CARRY_CAP_D = 7.5, BALL_CAP_D = 30.0;
const FPS_D = 60, DT_D = 1 / FPS_D;
const paced = typeof paceAdvance === 'function';
const BALL_TYPES_D = new Set(['Pass', 'Ball Receipt*', 'Carry', 'Shot', 'Clearance', 'Ball Recovery', 'Interception', 'Dribble',
  'Miscontrol', 'Goal Keeper', 'Block', 'Duel', 'Dispossessed', '50/50', 'Foul Won', 'Referee Ball-Drop', 'Shield']);
const bev = events.filter(e => BALL_TYPES_D.has(e.type) && e.x != null).sort((a, b) => a.t - b.t);
const bevT = bev.map(e => e.t);
const cuts = (MATCH_DATA.periods || []).slice(1).map(p => p.start);
const out = { matchId: MATCH_ID, paced };
for (const mode of ['tactical', 'realism']) {
  displayMode = mode;
  if (typeof resetPacing === 'function') resetPacing();
  const tracks = [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks];
  let t = 0, frames = 0, playerFrames = 0, over8 = 0, over95 = 0, anyOver8 = 0, playback = 0;
  let ballOver = 0, stillThenJump = 0, skipFrames = 0;
  const causes = {};
  const prevP = new Map();
  let prevB = null, stillFor = 0;
  const end = typeof maxT === 'number' ? maxT : 5400;
  while (t < end) {
    const t1 = paced ? paceAdvance(t, DT_D) : t + DT_D;
    playback += DT_D;
    const cut = cuts.some(c => t < c && t1 >= c);
    const skipping = paced && typeof inSkip === 'function' && inSkip(t1);
    if (skipping) skipFrames++;
    let frameMax = 0;
    for (const tr of tracks) {
      if (!trackActiveAt(tr, t1)) { prevP.delete(tr); continue; }
      const p = playerPositionAt(tr, t1);
      const q = prevP.get(tr);
      if (q && !cut && !skipping && trackActiveAt(tr, t)) {
        const v = Math.hypot(p[0] - q[0], p[1] - q[1]) / DT_D;
        playerFrames++; if (v > RUN_CAP_D * 1.01) over8++; if (v > 9.5) over95++;
        frameMax = Math.max(frameMax, v);
      }
      prevP.set(tr, p);
    }
    if (frameMax > RUN_CAP_D * 1.01) anyOver8++;
    const b = ballHomePositionAt(t1);
    if (prevB && !cut && !skipping) {
      const v = Math.hypot(b[0] - prevB[0], b[1] - prevB[1]) / DT_D;
      if (v > BALL_CAP_D * 1.01) {
        ballOver++;
        if (stillFor >= 0.5) {
          stillThenJump++;
          const i = bsearch(bevT, t1 + 0.02), e = bev[i], p = bev[i - 1];
          let c = 'other';
          if (e && Math.abs(e.t - t1) < 0.05 && p) {
            if ((p.type === 'Pass' || p.type === 'Shot') && Math.abs(p.t + p.dur - e.t) < 0.05) c = 'flight end -> next event, same instant';
            else if (p.playerId === e.playerId) c = 'same player, next own event (no carry)';
            else if (p.type === 'Ball Receipt*') c = 'receipt -> other player event';
            else c = 'handoff to other player event';
          } else if (e && e.type === 'Carry') c = 'carry boundary';
          else c = 'rest, then move before next event';
          causes[c] = (causes[c] || 0) + 1;
        }
      }
      stillFor = v < 0.3 ? stillFor + DT_D : 0;
    }
    prevB = b;
    frames++;
    t = t1;
  }
  Object.assign(out, {
    [mode + '_match_s']: +end.toFixed(1), [mode + '_playback_s_1x']: +playback.toFixed(1),
    [mode + '_player_frames_gt8']: +(over8 / playerFrames).toFixed(5), [mode + '_player_frames_gt9_5']: +(over95 / playerFrames).toFixed(5),
    [mode + '_frames_any_player_gt8']: +(anyOver8 / frames).toFixed(4),
    [mode + '_ball_frames_gt30']: ballOver, [mode + '_ball_still_then_jump']: stillThenJump, [mode + '_ball_jump_causes']: causes,
    [mode + '_skip_frames']: skipFrames,
  });
}
console.log(JSON.stringify(out));
