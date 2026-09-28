// Dump every active player's displayed position at T (env FRAME_T) in both modes.
// Output: JSON lines {mode, team, name, own:[x,y], shared:[x,y]} where shared is
// the default "Match Ends" pitch (period 1: home as-is, away mirrored).
const T = parseFloat(process.env.FRAME_T || '93.743');
const out = [];
const ev = events.reduce((best, e) => (e.t <= T && (!best || e.t >= best.t) ? e : best), null);
const period = ev ? ev.period : 1;
for (const mode of ['realism', 'tactical']) {
  displayMode = mode;
  for (const side of ['home', 'away']) {
    for (const tr of MATCH_DATA[side].tracks) {
      if (!trackActiveAt(tr, T)) continue;
      const [x, y] = playerPositionAt(tr, T);
      const isHome = side === 'home';
      let sx = x, sy = y;
      const flip = period === 1 ? !isHome : isHome;
      if (flip) { sx = PITCH_LEN_M - x; sy = PITCH_WID_M - y; }
      out.push({ mode, team: MATCH_DATA[side].name, name: tr.name, own: [+x.toFixed(2), +y.toFixed(2)], shared: [+sx.toFixed(2), +sy.toFixed(2)] });
    }
  }
}
console.log(JSON.stringify({ T, period, defaultMode: 'realism', rows: out }));
