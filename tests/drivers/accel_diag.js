// How quickly drawn players change speed, on screen: speed over 1 s of 1x
// PLAYBACK, and its change over the next 1 s (the same measure as
// tests/analysis/accel_caps.py on real tracking: p99 2.63, p99.9 4.06 m/s^2).
// Stoppage skips and period cuts excluded. Also: how far the drawn position
// is from the unretimed reconstruction (when rawPlayerPositionAt exists).
// Usage: node tests/viewer_harness.js <id> tests/drivers/accel_diag.js
const HZ_A = 5, W_A = 5;                     // 0.2 s steps, 1 s windows
const REAL_P999 = 4.06;
const out = { matchId: MATCH_ID };
for (const mode of (process.env.ACCEL_MODES || 'tactical').split(',')) {
  displayMode = mode;
  const tracks = [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks];
  const hist = new Map(tracks.map(tr => [tr, []]));   // recent [x, y, ok]
  const acc = [], worst = [];
  let t = 0, shiftSum = 0, shiftN = 0, shiftMax = 0;
  const cuts = (MATCH_DATA.periods || []).slice(1).map(p => p.start);
  const hasPace = typeof paceAdvance === 'function';
  while (t < maxT) {
    const t1 = hasPace ? paceAdvance(t, 1 / HZ_A) : t + 1 / HZ_A;
    const broken = (hasPace && (inSkip(t) || inSkip(t1))) || cuts.some(c => t < c && t1 >= c);
    for (const tr of tracks) {
      const h = hist.get(tr);
      if (!trackActiveAt(tr, t1) || broken) { h.length = 0; continue; }
      const p = playerPositionAt(tr, t1);
      h.push([p[0], p[1], t1]);
      if (h.length > 2 * W_A + 1) h.shift();
      if (h.length === 2 * W_A + 1) {
        const v0 = Math.hypot(h[W_A][0] - h[0][0], h[W_A][1] - h[0][1]);
        const v1 = Math.hypot(h[2 * W_A][0] - h[W_A][0], h[2 * W_A][1] - h[W_A][1]);
        const a = Math.abs(v1 - v0);
        acc.push(a);
        if (a > REAL_P999) worst.push({ name: tr.name, t: +h[W_A][2].toFixed(2), a: +a.toFixed(1), v0: +v0.toFixed(1), v1: +v1.toFixed(1) });
      }
      if (typeof rawPlayerPositionAt === 'function' && (Math.round(t1 * HZ_A) % 5 === 0)) {
        const r = rawPlayerPositionAt(tr, t1), d = Math.hypot(r[0] - p[0], r[1] - p[1]);
        shiftSum += d; shiftN++; shiftMax = Math.max(shiftMax, d);
      }
    }
    t = t1;
  }
  acc.sort((a, b) => a - b);
  const q = p => +acc[Math.min(acc.length - 1, Math.floor(p * acc.length))].toFixed(2);
  worst.sort((a, b) => b.a - a.a);
  Object.assign(out, {
    [mode + '_accel_p50']: q(0.5), [mode + '_accel_p90']: q(0.9), [mode + '_accel_p99']: q(0.99), [mode + '_accel_p999']: q(0.999),
    [mode + '_share_over_real_p999']: +(acc.filter(a => a > REAL_P999).length / acc.length).toFixed(5),
    [mode + '_share_over_6']: +(acc.filter(a => a > 6).length / acc.length).toFixed(5),
    [mode + '_worst']: worst.slice(0, 8),
    ...(shiftN ? { [mode + '_retime_shift_mean_m']: +(shiftSum / shiftN).toFixed(3), [mode + '_retime_shift_max_m']: +shiftMax.toFixed(2) } : {}),
  });
}
console.log(JSON.stringify(out));
