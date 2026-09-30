// Polish item 2 evidence: the ball in the four situations named in the brief,
// on either viewer (pre-polish or paced). Per situation, over every instance:
//   jumps      windows with any on-screen ball step faster than 30 m/s
//   still      windows where the ball stays put >= 0.5 s while the player who
//              has it moves >= 2 m, then jumps (the "stays still, then
//              teleports" pattern)
//   gap_m      ball-to-player distance at the middle of the window (median / p90)
// Situations (from the payload's real events):
//   carry_no_end      a Carry with no end location or no duration, until the
//                     same player's next on-ball event
//   after_carry       a Carry's end until that player's next on-ball event
//   receipt_no_carry  a Ball Receipt* followed directly by another on-ball
//                     event of the same player that is not a Carry
//   reception_window  a completed pass in flight until 0.5 s after its receipt
const paced = typeof paceAdvance === 'function';
displayMode = 'tactical';
const BT = new Set(['Pass', 'Ball Receipt*', 'Carry', 'Shot', 'Clearance', 'Ball Recovery', 'Interception', 'Dribble',
  'Miscontrol', 'Goal Keeper', 'Block', 'Duel', 'Dispossessed', '50/50', 'Foul Won', 'Referee Ball-Drop', 'Shield']);
const ev = events.filter(e => BT.has(e.type) && e.x != null && !(e.type === 'Ball Receipt*' && e.receiptOutcome)).sort((a, b) => a.t - b.t);
const trOf = new Map([...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks].map(t => [t.id, t]));
const W = [];
for (let i = 0; i + 1 < ev.length; i++) {
  const e = ev[i], n = ev[i + 1], tr = trOf.get(e.playerId);
  if (!tr) continue;
  if (e.type === 'Carry' && (e.endX == null || !(e.dur > 0)) && n.playerId === e.playerId && n.t - e.t > 0.3) W.push(['carry_no_end', tr, e.t, n.t]);
  if (e.type === 'Carry' && e.endX != null && e.dur > 0 && n.playerId === e.playerId && n.t - (e.t + e.dur) > 0.3) W.push(['after_carry', tr, e.t + e.dur, n.t]);
  if (e.type === 'Ball Receipt*' && n.playerId === e.playerId && n.type !== 'Carry' && n.t - e.t > 0.3) W.push(['receipt_no_carry', tr, e.t, n.t]);
  if (e.type === 'Pass' && e.passOutcome === 'Complete' && n.type === 'Ball Receipt*' && trOf.get(n.playerId) && e.dur > 0.2)
    W.push(['reception_window', trOf.get(n.playerId), e.t, n.t + 0.5]);
}
const ballAt = t => (paced ? ballHomePositionAt(t) : ballHomePositionAt(t));
const plAt = (tr, t) => { const p = playerPositionAt(tr, t); return toHomeFrame(p[0], p[1], tr.isHome); };
const acc = {};
for (const [kind, tr, a, b] of W) {
  const r = acc[kind] || (acc[kind] = { n: 0, jumps: 0, still: 0, gaps: [], maxv: [] });
  r.n++;
  let t = a, prev = ballAt(a), stillFor = 0, jumped = false, stillJump = false, maxv = 0, moved = 0, pp = plAt(tr, a);
  while (t < b) {
    const t1 = paced ? paceAdvance(t, 1 / 60) : t + 1 / 60;
    const p = ballAt(t1), q = plAt(tr, t1);
    const v = Math.hypot(p[0] - prev[0], p[1] - prev[1]) * 60;
    maxv = Math.max(maxv, v);
    if (v > 30.3) { jumped = true; if (stillFor >= 0.5 && moved >= 2) stillJump = true; }
    if (v < 0.3) { stillFor += t1 - t; moved += Math.hypot(q[0] - pp[0], q[1] - pp[1]); } else { stillFor = 0; moved = 0; }
    prev = p; pp = q; t = t1;
  }
  const tm = (a + b) / 2, bm = ballAt(tm), pm = plAt(tr, tm);
  if (kind !== 'reception_window') r.gaps.push(Math.hypot(bm[0] - pm[0], bm[1] - pm[1]));
  else { const be = ballAt(b - 0.5), pe = plAt(tr, b - 0.5); r.gaps.push(Math.hypot(be[0] - pe[0], be[1] - pe[1])); }
  r.maxv.push(maxv);
  if (jumped) r.jumps++;
  if (stillJump) r.still++;
}
const q = (a, p) => { const s = a.slice().sort((x, y) => x - y); return s.length ? +s[Math.min(s.length - 1, Math.floor(p * s.length))].toFixed(2) : null; };
const out = { matchId: MATCH_ID, paced };
for (const [k, r] of Object.entries(acc))
  out[k] = { windows: r.n, with_jump_gt30: r.jumps, still_then_jump: r.still, ball_to_player_m_median: q(r.gaps, 0.5), ball_to_player_m_p90: q(r.gaps, 0.9),
    ball_max_mps_median: q(r.maxv, 0.5), ball_max_mps_p99: q(r.maxv, 0.99) };
console.log(JSON.stringify(out));
