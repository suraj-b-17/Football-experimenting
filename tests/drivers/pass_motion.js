// For each pass in PASSES_JSON: every active outfield player's displayed movement
// during the flight (path length through 0/25/50/75/100%) and in the 2 s after
// reception (path length sampled every 0.25 s), per display mode.
const fsm = require('fs');
const passes = JSON.parse(fsm.readFileSync(process.env.PASSES_JSON, 'utf8'));
const roles = JSON.parse(fsm.readFileSync(process.env.ROLES_JSON, 'utf8'))[MATCH_ID] || {};
const modes = (process.env.MODES || 'realism,tactical').split(',');
function pathLen(tr, ts) {
  let d = 0, prev = null;
  for (const t of ts) { const p = playerPositionAt(tr, t); if (prev) d += Math.hypot(p[0] - prev[0], p[1] - prev[1]); prev = p; }
  return d;
}
const med = a => { const s = [...a].sort((x, y) => x - y); const n = s.length; return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : NaN; };
const res = [];
for (const mode of modes) {
  displayMode = mode;
  for (const p of passes) {
    const tEnd = p.t + p.dur;
    const flightTs = [0, 0.25, 0.5, 0.75, 1].map(f => p.t + f * p.dur);
    const postTs = []; for (let k = 0; k <= 8; k++) postTs.push(tEnd + k * 0.25);
    const fl = [], po = [], rows = [];
    for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) {
      if (roles[String(tr.id)] === 'GK') continue;
      if (!trackActiveAt(tr, p.t) || !trackActiveAt(tr, tEnd + 2)) continue;
      const f = pathLen(tr, flightTs), q = pathLen(tr, postTs);
      fl.push(f); po.push(q);
      rows.push({ name: tr.name, pos: flightTs.map(t => playerPositionAt(tr, t).slice(0, 2).map(v => +v.toFixed(1))), flight: +f.toFixed(2), post2s: +q.toFixed(2) });
    }
    res.push({ mode, label: p.label, n: fl.length,
      flight_median: +med(fl).toFixed(2), flight_max: +Math.max(...fl).toFixed(2), flight_under1m: fl.filter(v => v < 1).length,
      post_median: +med(po).toFixed(2), post_max: +Math.max(...po).toFixed(2), post_under1m: po.filter(v => v < 1).length,
      peak_team_speed_mps: +(med(fl) / p.dur).toFixed(2), rows });
  }
}
console.log(JSON.stringify(res));
