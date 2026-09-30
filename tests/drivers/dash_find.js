// Finds "super dash" episodes in the PRE-polish viewer: a player drawn above
// RUN_CAP (8 m/s) for >= 0.15 s, Tactical Clarity (the default), 60 fps.
// Each is classified by what was being drawn at its peak.
// Usage: APP_JS=tests/baseline/app_before_polish.js DATA_DIR=<before data> node tests/viewer_harness.js <id> tests/drivers/dash_find.js
displayMode = 'tactical';
const FPS = 60, DT = 1 / FPS, CAP = 8.0;
const kicks = (MATCH_DATA.kickoffs || []).map(k => k[0]);
const cutsF = (MATCH_DATA.periods || []).slice(1).map(p => p.start);
const receipts = new Map();
for (const e of events) if (e.type === 'Ball Receipt*' && !e.receiptOutcome) {
  if (!receipts.has(e.playerId)) receipts.set(e.playerId, []);
  receipts.get(e.playerId).push(e.t);
}
const eps = [];
for (const tr of [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks]) {
  for (const [a, b] of tr.intervals) {
    let prev = null, run = null;
    for (let t = a; t <= b; t += DT) {
      const p = playerPositionAt(tr, t);
      if (prev && !cutsF.some(c => t - DT < c && t >= c)) {
        const v = Math.hypot(p[0] - prev[0], p[1] - prev[1]) / DT;
        if (v > CAP) { if (!run) run = { t0: t - DT, peak: 0, tp: t }; if (v > run.peak) { run.peak = v; run.tp = t; } run.t1 = t; }
        else if (run) { if (run.t1 - run.t0 >= 0.15) eps.push({ tr, ...run }); run = null; }
      }
      prev = p;
    }
  }
}
const out = eps.map(e => {
  const t = e.tp, tr = e.tr;
  const carry = tr.carryObjs.find(c => t >= c.t0 - 0.02 && t <= c.tEnd + 0.52);
  const rec = (tr.recObjs || []).find(r => t >= r.t_pass && t <= r.t_receive);
  const rcpt = (receipts.get(tr.id) || []).find(x => x >= e.t0 - 0.05 && x <= e.t1 + 1.0);
  let cls = 'reconstruction between anchors';
  if (carry) cls = 'carry';
  else if (kicks.some(k => Math.abs(k - t) < 3)) cls = 'kickoff';
  else if (rec || rcpt !== undefined) cls = 'off-ball run to receive a pass';
  const A = tr.anchors.filter(a => a[3] !== 4), lo = A.filter(a => a[0] <= e.t0).pop(), hi = A.find(a => a[0] >= e.t1);
  const implied = lo && hi ? Math.hypot(hi[1] - lo[1], hi[2] - lo[2]) / Math.max(hi[0] - lo[0], 1e-3) : null;
  return { player: tr.name, pid: tr.id, t0: +e.t0.toFixed(3), t1: +e.t1.toFixed(3), dur: +(e.t1 - e.t0).toFixed(2), peak_mps: +e.peak.toFixed(1), cls,
    carry: carry ? { t0: carry.t0, t1: carry.t1, len: +carry.len.toFixed(1), real_mps: +(carry.len / Math.max(carry.t1 - carry.t0, 1e-3)).toFixed(1) } : null,
    bracketing_anchor_mps: implied == null ? null : +implied.toFixed(1) };
});
const byCls = {};
for (const o of out) byCls[o.cls] = (byCls[o.cls] || 0) + 1;
console.log(JSON.stringify({ matchId: MATCH_ID, episodes: out.length, byCls, top: out.sort((a, b) => b.peak_mps - a.peak_mps).slice(0, 60) }));
