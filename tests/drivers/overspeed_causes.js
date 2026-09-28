// Classify every 60 fps frame with displayed speed > 9.5 m/s by its cause (old viewer).
const fso = require('fs');
const carriesO = JSON.parse(fso.readFileSync(process.env.CARRIES_JSON, 'utf8'))[MATCH_ID];
const DTo = 1 / 60;
const out = {};
for (const mode of ['tactical', 'realism']) {
  displayMode = mode;
  const cause = {}, dist = {};
  const bump = (k, d) => { cause[k] = (cause[k] || 0) + 1; dist[k] = (dist[k] || 0) + d; };
  for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) {
    const W = tr.waypoints;
    const recEnds = (tr.receptions || []).map(r => [r.t_pass, r.t_receive]);
    const myCarries = carriesO.filter(c => c.pid === tr.id);
    let prev = null;
    for (let t = 0; t <= maxT; t += DTo) {
      if (!trackActiveAt(tr, t)) { prev = null; continue; }
      const p = playerPositionAt(tr, t);
      if (prev) {
        const d = Math.hypot(p[0] - prev[0], p[1] - prev[1]);
        if (d / DTo > 9.5) {
          let k;
          const lo = W.filter(w => w[0] <= t).pop(), hi = W.find(w => w[0] > t);
          const segV = lo && hi ? Math.hypot(hi[1] - lo[1], hi[2] - lo[2]) / Math.max(1e-3, hi[0] - lo[0]) : 0;
          if (t < 2) k = 'kickoff seed (t=0 template)';
          else if (mode === 'tactical' && recEnds.some(([a, b]) => t - DTo <= b && t >= b)) k = 'reception window ends (snap)';
          else if (mode === 'tactical' && recEnds.some(([a, b]) => t >= a && t <= b)) k = 'inside reception ease';
          else if (myCarries.some(c => t >= c.t - 0.05 && t <= c.t + c.dur + 0.05 && c.dur > 0 && c.len / c.dur > 9.5)) k = 'carry with real speed > 9.5 (data anomaly)';
          else if (lo && hi && hi[0] - lo[0] < 0.5 && segV > 9.5) k = 'two anchors <0.5 s apart, implied > 9.5 m/s';
          else if (segV > 9.5) k = 'anchor segment implies > 9.5 m/s';
          else if (myCarries.some(c => t >= c.t && t <= c.t + c.dur)) k = 'during a carry, anchors imply <= 9.5 (display artefact)';
          else k = 'other (display artefact)';
          bump(k, d);
        }
      }
      prev = p;
    }
  }
  out[mode] = Object.fromEntries(Object.entries(cause).sort((a, b) => b[1] - a[1]).map(([k, v]) => [k, { frames: v, total_m: +dist[k].toFixed(1) }]));
}
console.log(JSON.stringify(out));
