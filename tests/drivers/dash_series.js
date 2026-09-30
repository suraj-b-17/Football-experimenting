// On-screen speed over PLAYBACK time (1x, 60 fps) for chosen players and
// windows, on either viewer: pre-polish (match time == playback time) or paced.
// Examples come from env DASH_EXAMPLES: JSON [{pid, t0, t1, label}].
displayMode = 'tactical';
const EX = JSON.parse(process.env.DASH_EXAMPLES);
const pacedS = typeof paceAdvance === 'function';
const DTs = 1 / 60, PAD = 2;
const res = [];
for (const ex of EX) {
  const tr = [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks].find(t => t.id === ex.pid);
  const pos = t => {
    if (pacedS) { const f = framePositions(t).find(q => q.tr === tr); return [f.x, f.y]; }
    const p = playerPositionAt(tr, t); return toHomeFrame(p[0], p[1], tr.isHome);
  };
  let t = ex.t0 - PAD, pb = 0, prev = pos(t);
  const series = [];
  while (t < ex.t1 + PAD) {
    const t1 = pacedS ? paceAdvance(t, DTs) : t + DTs;
    const p = pos(t1);
    pb += DTs;
    series.push([+pb.toFixed(4), +t1.toFixed(4), +(Math.hypot(p[0] - prev[0], p[1] - prev[1]) / DTs).toFixed(3), pacedS && (inSkip(t1) || inSkip(t)) ? 1 : 0]);
    prev = p; t = t1;
  }
  const inDash = series.filter(s => s[1] >= ex.t0 && s[1] <= ex.t1 && !s[3]);
  res.push({ ...ex, paced: pacedS,
    dash_playback_s: +(pacedS ? playbackBetween(ex.t0, ex.t1) : ex.t1 - ex.t0).toFixed(3),
    window_playback_s: +pb.toFixed(3),
    peak_mps: Math.max(...inDash.map(s => s[2])), series });
}
console.log(JSON.stringify(res));
