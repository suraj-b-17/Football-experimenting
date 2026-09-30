// Polish item 4: finds crowded on-ball moments (a tackle, a corner delivery, a
// crowded midfield duel) and measures them on either viewer:
//   overlap_share   share of 60 fps frames (+-1.5 s window) in which two drawn
//                   tokens within 8 m of the ball overlap by more than 1 px
//                   (token radius as drawn: fixed 9 px before, shrunk to fit
//                   its nearest neighbour now)
//   min_sep_m       smallest distance between two such players
//   ball_jumps      frames where the ball moves > 30 m/s on screen
//   player_max_mps  fastest on-screen player among them
//   owner_cue       (current viewer) share of frames where exactly one player
//                   is marked as on the ball while the ball is on someone
// With CLUSTER_TIMES (JSON [{name, t}]) it measures those moments instead of
// searching (used for the pre-polish viewer, so both see the same moments).
displayMode = 'tactical';
const paced = typeof paceAdvance === 'function';
const trs = [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks];
function drawn(t) {
  if (paced) return framePositions(t).map(f => ({ tr: f.tr, x: f.x, y: f.y, own: f.own }));
  return trs.filter(tr => trackActiveAt(tr, t)).map(tr => { const p = playerPositionAt(tr, t); const h = toHomeFrame(p[0], p[1], tr.isHome); return { tr, x: h[0], y: h[1], own: 0 }; });
}
function crowd(t, r = 5) { const b = ballHomePositionAt(t); return drawn(t).filter(f => Math.hypot(f.x - b[0], f.y - b[1]) < r).length; }
let picks;
if (process.env.CLUSTER_TIMES) picks = JSON.parse(process.env.CLUSTER_TIMES);
else {
  const cand = { corner: [], tackle: [], midfield: [] };
  for (const s of STOPS.filter(s => s.kind === 'corner')) {
    const ev = events.find(e => Math.abs(e.t - s.end) < 1e-3 && e.type === 'Pass');
    if (ev) cand.corner.push({ name: 'corner', t: s.end + ev.dur, c: crowd(s.end + ev.dur, 6), what: `corner by ${ev.playerName} arriving` });
  }
  for (const e of events) {
    if (e.type !== 'Duel' || e.x == null) continue;
    const [bx] = toHomeFrame(e.x, e.y, e.isHome), mid = bx > 38 && bx < 67;
    cand[mid ? 'midfield' : 'tackle'].push({ name: mid ? 'midfield duel' : 'tackle', t: e.t, c: crowd(e.t, 6), what: `Duel by ${e.playerName} at x=${bx.toFixed(0)}` });
  }
  picks = [];
  for (const k of ['corner', 'midfield', 'tackle']) {   // the most crowded of each, at least 20 s from the others
    const p = cand[k].sort((a, b) => b.c - a.c).find(p => picks.every(q => Math.abs(q.t - p.t) > 20));
    if (p) picks.push(p);
  }
}
const res = [];
for (const p of picks) {
  let overlapFrames = 0, minSep = Infinity, jumps = 0, pmax = 0, ownFrames = 0, onBallFrames = 0, frames = 0;
  let t = p.t - 1.5, prevB = ballHomePositionAt(t), prevP = new Map(drawn(t).map(f => [f.tr, f]));
  while (t < p.t + 1.5) {
    const t1 = paced ? paceAdvance(t, 1 / 60) : t + 1 / 60;
    const skipping = paced && (inSkip(t1) || inSkip(t));
    const b = ballHomePositionAt(t1), fr = drawn(t1), near = fr.filter(f => Math.hypot(f.x - b[0], f.y - b[1]) < 8);
    const ppm = metersToPixels(1);
    const rad = () => 9;   // fixed-radius tokens (shrink-to-fit was reverted per user feedback)
    let ov = false;
    for (let i = 0; i < near.length; i++) for (let j = i + 1; j < near.length; j++) {
      const d = Math.hypot(near[i].x - near[j].x, near[i].y - near[j].y);
      minSep = Math.min(minSep, d); if (d * ppm < rad(near[i]) + rad(near[j]) - 1) ov = true;
    }
    if (ov && !skipping) overlapFrames++;
    if (!skipping && Math.hypot(b[0] - prevB[0], b[1] - prevB[1]) * 60 > 30.3) jumps++;
    if (!skipping) for (const f of near) { const q = prevP.get(f.tr); if (q) pmax = Math.max(pmax, Math.hypot(f.x - q.x, f.y - q.y) * 60); }
    if (paced) { const o = ownerAt(t1); if (o) { onBallFrames++; if (fr.filter(f => f.own > 0.5).length === 1) ownFrames++; } }
    prevB = b; prevP = new Map(fr.map(f => [f.tr, f])); t = t1; if (!skipping) frames++;
  }
  const bb = ballHomePositionAt(p.t), cp = transformCoords(bb[0], bb[1], true, periodAt(p.t));
  res.push({ ...p, frames, overlap_share: +(overlapFrames / Math.max(frames, 1)).toFixed(2), min_sep_m: +minSep.toFixed(2), ball_jump_frames: jumps, player_max_mps: +pmax.toFixed(1),
    owner_cue_share: paced && onBallFrames ? +(ownFrames / onBallFrames).toFixed(2) : null,
    crop: [Math.round(Math.max(0, Math.min(960 - 320, cp.px - 160))), Math.round(Math.max(0, Math.min(620 - 220, cp.py - 110))), 320, 220] });
}
console.log(JSON.stringify(res));
