// Carry diagnosis at render rate (60 fps). Env: CARRIES_JSON, MODES, OUT_DETAIL (ids to dump per-frame).
const fsc = require('fs');
const FPS = 60, DT = 1 / FPS;
const carries = JSON.parse(fsc.readFileSync(process.env.CARRIES_JSON, 'utf8'))[MATCH_ID];
const detailIds = new Set((process.env.OUT_DETAIL || '').split(',').filter(Boolean));
const modes = (process.env.MODES || 'realism,tactical').split(',');
const byId = {};
for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) byId[tr.id] = { tr, isHome: side === 'home' };

// capture the drawn ball: drawEventAction draws the ball with ctx.arc(x, y, 5.5, ...)
let lastBall = null;
ctx.arc = (x, y, r) => { if (Math.abs(r - 5.5) < 1e-9) lastBall = [x, y]; };
const marginX = 40, marginY = 35, pitchW = canvas.width - 80, pitchH = canvas.height - 70;
const pxToPitch = (px, py) => [(px - marginX) / pitchW * PITCH_LEN_M, (py - marginY) / pitchH * PITCH_WID_M];
const sortedEvents = timeline.map(i => events[i]);
function eventAt(clock) {
  let lo = 0, hi = sortedEvents.length - 1;
  if (clock >= sortedEvents[hi].t) lo = hi;
  else if (clock > sortedEvents[0].t) { while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (sortedEvents[mid].t <= clock) lo = mid; else hi = mid; } }
  const cur = sortedEvents[lo];
  const nextT = lo + 1 < sortedEvents.length ? sortedEvents[lo + 1].t : cur.t + 1;
  const prog = nextT > cur.t ? Math.max(0, Math.min(1, (clock - cur.t) / (nextT - cur.t))) : 1;
  return [cur, prog];
}
function ballAt(clock) {
  if (typeof drawnBallAt === 'function') return drawnBallAt(clock);     // new viewer exposes it
  const [cur, prog] = eventAt(clock);
  lastBall = null; drawEventAction(cur, prog);
  return lastBall ? { px: lastBall, period: cur.period } : null;
}
function displayedPitch(tr, isHome, t, period) {
  const p = playerPositionAt(tr, t);
  const c = transformCoords(p[0], p[1], isHome, period);
  return pxToPitch(c.px, c.py);
}
const res = {};
for (const mode of modes) {
  displayMode = mode;
  const rows = [], detail = {};
  for (const c of carries) {
    const P = byId[c.pid]; if (!P) continue;
    const period = eventAt(c.t)[0].period || 1;
    let maxV = 0, over = 0, path = 0, prev = null, ballGapMax = 0, ballGapSum = 0, n = 0;
    const det = [];
    for (let t = c.t - 0.5; t <= c.t + c.dur + 0.5 + 1e-9; t += DT) {
      const p = displayedPitch(P.tr, P.isHome, t, period);
      if (prev) {
        const v = Math.hypot(p[0] - prev[0], p[1] - prev[1]) / DT;
        maxV = Math.max(maxV, v); if (v > 9.5) over++;
        if (t > c.t && t <= c.t + c.dur) path += v * DT;
      }
      let bgap = null;
      if (t >= c.t && t <= c.t + c.dur) {
        const b = ballAt(t);
        if (b) { const bp = b.px ? pxToPitch(b.px[0], b.px[1]) : b.pitch; bgap = Math.hypot(bp[0] - p[0], bp[1] - p[1]); ballGapMax = Math.max(ballGapMax, bgap); ballGapSum += bgap; n++; }
      }
      if (detailIds.has(c.id)) det.push({ t: +t.toFixed(3), x: +p[0].toFixed(2), y: +p[1].toFixed(2), v: prev ? +(Math.hypot(p[0] - prev[0], p[1] - prev[1]) / DT).toFixed(2) : null, ball_gap: bgap == null ? null : +bgap.toFixed(2) });
      prev = p;
    }
    rows.push({ id: c.id, name: c.name, t: c.t, dur: c.dur, len: c.len, real_v: c.dur > 0 ? c.len / c.dur : null,
      disp_mean_v: c.dur > 0 ? path / c.dur : null, max_v: maxV, frames_over_9_5: over,
      ball_gap_mean: n ? ballGapSum / n : null, ball_gap_max: ballGapMax });
    if (detailIds.has(c.id)) detail[c.id] = det;
  }
  // global single-frame jumps, whole match, every active player
  const jumps = [];
  for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) {
    let prev = null;
    for (let t = 0; t <= maxT; t += DT) {
      if (!trackActiveAt(tr, t)) { prev = null; continue; }
      const p = playerPositionAt(tr, t);
      if (prev) { const d = Math.hypot(p[0] - prev[0], p[1] - prev[1]); if (d > 9.5 * DT) jumps.push([d, tr.name, t]); }
      prev = p;
    }
  }
  jumps.sort((a, b) => b[0] - a[0]);
  const wpOf = name => { for (const s of ['home', 'away']) for (const tr of MATCH_DATA[s].tracks) if (tr.name === name) return tr.anchors || tr.waypoints; };
  const top = jumps.slice(0, 10).map(([d, name, t]) => ({ name, t: +t.toFixed(3), dist_m: +d.toFixed(2), speed_mps: +(d / DT).toFixed(1),
    nearby: (wpOf(name) || []).filter(w => Math.abs(w[0] - t) < 2).map(w => w.slice(0, 3).map(v => +(+v).toFixed(2))) }));
  res[mode] = { rows, detail, frames_over_9_5_total: jumps.length, top_jumps: top };
}
console.log(JSON.stringify(res));
