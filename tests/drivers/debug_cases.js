// Debug: explain specific QA flags (env CASES = JSON list of {name, t, win}).
const casesD = JSON.parse(process.env.CASES);
const all = [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks];
for (const c of casesD) {
  const tr = all.find(x => x.name.startsWith(c.name));
  displayMode = c.mode || 'tactical';
  console.log('==', c.name, c.t, displayMode);
  console.log(' anchors near', JSON.stringify(tr.anchors.filter(a => Math.abs(a[0] - c.t) < (c.win || 3))));
  console.log(' carries near', JSON.stringify(tr.carryObjs.filter(k => Math.abs(k.t0 - c.t) < (c.win || 3)).map(k => ({ t0: k.t0, t1: k.t1, tEnd: k.tEnd, ok: k.ok, anom: k.anom, r: +k.r.toFixed(2), len: +k.len.toFixed(2) }))));
  console.log(' receptions near', JSON.stringify(tr.recObjs.filter(k => Math.abs(k.t_pass - c.t) < (c.win || 3))));
  let prev = null;
  for (let t = c.t - (c.before || 0.5); t <= c.t + (c.after || 1.0); t += 1 / 60) {
    const p = playerPositionAt(tr, t), b = baseAt(tr, t, displayMode);
    const v = prev ? Math.hypot(p[0] - prev[0], p[1] - prev[1]) * 60 : 0;
    if (Math.round((t - c.t) * 60) % (c.every || 6) === 0) console.log(`  t=${t.toFixed(3)} pos=(${p[0].toFixed(2)},${p[1].toFixed(2)}) base=(${b[0].toFixed(2)},${b[1].toFixed(2)}) v=${v.toFixed(1)}`);
    prev = p;
  }
}
