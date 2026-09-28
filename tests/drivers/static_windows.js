// Rolling 60 s windows (5 s step) on the displayed path, sampled at 1 Hz; counts
// windows with <3 m path length, by role, and how many real anchors (payload
// waypoints, excluding the t=0 seed) fall inside those windows.
const fsw = require('fs');
const roles = JSON.parse(fsw.readFileSync(process.env.ROLES_JSON, 'utf8'))[MATCH_ID] || {};
const modes = (process.env.MODES || 'tactical').split(',');
const out = {};
for (const mode of modes) {
  displayMode = mode;
  const agg = {};
  for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) {
    const role = roles[String(tr.id)] || 'FWD';
    const a = agg[role] || (agg[role] = { windows: 0, under3m: 0, sparse: 0, dense: 0 });
    const ivs = typeof activeIntervals === 'function' ? activeIntervals(tr) : [[tr.enter ?? 0, tr.exit ?? maxT]];
    const anchorTs = (tr.anchors || tr.waypoints).map(w => w[0]).filter(t => t > 0.001);
    for (const [a0, a1] of ivs) {
      for (let w0 = a0; w0 + 60 <= a1; w0 += 5) {
        let d = 0, prev = null;
        for (let t = w0; t <= w0 + 60 + 1e-9; t += 1) { const p = playerPositionAt(tr, t); if (prev) d += Math.hypot(p[0] - prev[0], p[1] - prev[1]); prev = p; }
        a.windows++;
        if (d < 3) {
          a.under3m++;
          const n = anchorTs.filter(t => t >= w0 && t <= w0 + 60).length;
          if (n <= 1) a.sparse++; else a.dense++;
        }
      }
    }
  }
  out[mode] = agg;
}
console.log(JSON.stringify(out));
