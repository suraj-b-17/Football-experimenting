// Match viewer. Loads output/data/<match>.json (pipeline/build_payload.py).
//
// Both display modes draw the SAME smoothed reconstruction (per-player segments
// on a 1 s grid plus every real anchor time, so real anchors are knots of the
// displayed path). Differences:
//   Full Realism     all knots, uncertainty rings.
//   Tactical Clarity non-anchor knots thinned to every 3 s (less wobble; every
//                    anchor kept), pass anticipation, stoppage compression with
//                    labels, no rings.
// In both modes a Carry is drawn from its real start to its real end over its
// real duration, and the ball is drawn from the same ball path the model uses.

// Works from file:// (double-click): match data is a plain script,
// data/<id>.js, that sets window.MATCH_DATA_<id>. No fetch/XHR anywhere.
// The match id comes from ?match=<id> or #match=<id>.
const params = new URLSearchParams(location.search);
const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
const MATCH_ID = [params.get('match'), hashParams.get('match'), '3754129'].find(v => v && /^\d+$/.test(v));
const DEFAULT_MODE = 'tactical';

let MATCH_DATA = null;
let canvas, ctx;
let currentIndex = 0, isPlaying = false, playbackSpeed = 1.0, lastTimestamp = 0;
let showTrails = true, showUncertainty = true, matchEndsMode = true;
let filterTeam = 'all', filterType = 'all', filterPlayer = 'all';
let displayMode = params.get('mode') || hashParams.get('mode') || DEFAULT_MODE;
let activeStoppage = null, activeStoppageSpeed = 0;
let matchClock = 0;
let events = [], timeline = [], totalEvents = 0, maxT = 6000;
let PITCH_LEN_M = 105, PITCH_WID_M = 68;
const BASE_SPS = 1.0;
const MAX_SPEED_MPS = 9.5;
const STOPPAGE_WARP_REAL_SECONDS = 1.5;
const TACTICAL_KNOT_S = 3;
const BALL_OFFSET_M = 0.7;
const HARD_KINDS = new Set([0, 1, 2, 3]);
// Display-only "real spacing": stretches each team's outfield shape about its
// centroid by 1.2 (matches real team length/width/back-line spread in held-out
// tracking) but makes held-out positional error WORSE (9.68 -> 9.91 m), so it is
// off by default and never presented as more accurate. Faded to 0 within
// SPACING_FADE_S of a player's real anchor, so anchors are still hit exactly.
const SPACING_K = 1.2, SPACING_FADE_S = 10;
let realSpacing = false;

async function boot() {
  canvas = document.getElementById('pitchCanvas');
  ctx = canvas.getContext('2d');
  try {
    MATCH_DATA = await loadMatchScript(MATCH_ID);
  } catch (e) {
    const el = document.getElementById('load-error');
    el.textContent = `${e.message}. Keep viewer.html next to the data/ folder. If this browser blocks scripts on local files, ` +
      `open the output folder through any local web server instead.`;
    el.classList.remove('hidden');
    console.error(e);
    return;
  }
  PITCH_LEN_M = MATCH_DATA.pitchLengthM || 105;
  PITCH_WID_M = MATCH_DATA.pitchWidthM || 68;
  events = MATCH_DATA.events;
  totalEvents = events.length;
  maxT = MATCH_DATA.maxT;
  timeline = events.map((e, i) => i).sort((a, b) => events[a].t - events[b].t);
  prepareTracks();
  initHeader();
  buildRunningStats();
  wireControls();
  onEventChanged();
  requestAnimationFrame(render);
}

function loadMatchScript(id) {
  const key = 'MATCH_DATA_' + id;
  if (window[key]) return Promise.resolve(window[key]);
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = `data/${id}.js`;
    s.onload = () => (window[key] ? resolve(window[key]) : reject(new Error(`data/${id}.js loaded but did not define ${key}`)));
    s.onerror = () => reject(new Error(`Could not load data/${id}.js`));
    document.head.appendChild(s);
  });
}

// ---- numeric helpers ---------------------------------------------------------

function bsearch(arr, t) { // last index i with arr[i] <= t (or -1)
  let lo = -1, hi = arr.length;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (arr[m] <= t) lo = m; else hi = m; }
  return lo;
}

function pchipSlopes(t, v) {
  const n = t.length, m = new Float64Array(n);
  if (n < 2) return m;
  const d = new Float64Array(n - 1), h = new Float64Array(n - 1);
  for (let i = 0; i < n - 1; i++) { h[i] = t[i + 1] - t[i]; d[i] = (v[i + 1] - v[i]) / h[i]; }
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] * d[i] <= 0) m[i] = 0;
    else { const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1]; m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]); }
  }
  return m;
}

function hermite(p0, m0, p1, m1, s, h) {
  const s2 = s * s, s3 = s2 * s;
  return (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * h * m0 + (-2 * s3 + 3 * s2) * p1 + (s3 - s2) * h * m1;
}

// Hermite's own derivative (d/ds of the basis, /h for d/dt) — used only to
// detect mid-segment overshoot below, not for rendering.
function hermiteVel(p0, m0, p1, m1, s, h) {
  const s2 = s * s;
  const d00 = 6 * s2 - 6 * s, d10 = 3 * s2 - 4 * s + 1, d01 = -6 * s2 + 6 * s, d11 = 3 * s2 - 2 * s;
  return (d00 * p0 + d10 * h * m0 + d01 * p1 + d11 * h * m1) / h;
}
const OVERSHOOT_CAP_MPS = 12; // QA bar: pipeline-made speed above this gets smoothed

function makeCurve(t, x, y, sd) {
  const mx = pchipSlopes(t, x), my = pchipSlopes(t, y);
  // Two knots close together in time (a reception 0.04s before its carry_end,
  // an anchor right after a substitution, ...) give PCHIP an ill-conditioned
  // tangent there (a tiny denominator in the weighted-harmonic-mean formula),
  // which overshoots well past any real speed in the NEXT segment too, since
  // Hermite uses both endpoint tangents. Clamp jointly on (x,y), not per axis,
  // so direction is preserved (found via QA: single-frame jumps up to 25 m/s
  // next to short knot gaps — CHANGELOG_fix.md D11).
  for (let i = 0; i < mx.length; i++) {
    const spd = Math.hypot(mx[i], my[i]);
    if (spd > MAX_SPEED_MPS) { const s = MAX_SPEED_MPS / spd; mx[i] *= s; my[i] *= s; }
  }
  // Even with clamped endpoint tangents, a Hermite segment between two
  // anchors that are themselves close to the 9.5 m/s cap can still bulge
  // in the MIDDLE of the segment past it (found via season QA: real anchors
  // implying ~9.5 m/s, displayed 12-14 m/s). Anchors are exact at s=0/1
  // regardless of which curve is used between them, so segments that would
  // overshoot fall back to plain linear interpolation, which by construction
  // cannot exceed the endpoint-to-endpoint average speed (D15).
  const n = x.length;
  const useLinear = new Uint8Array(Math.max(0, n - 1));
  for (let i = 0; i < n - 1; i++) {
    const h = t[i + 1] - t[i];
    if (h <= 0) continue;
    let peak = 0;
    for (const s of [0.2, 0.4, 0.5, 0.6, 0.8]) {
      const vx = hermiteVel(x[i], mx[i], x[i + 1], mx[i + 1], s, h);
      const vy = hermiteVel(y[i], my[i], y[i + 1], my[i + 1], s, h);
      const v = Math.hypot(vx, vy);
      if (v > peak) peak = v;
    }
    if (peak > OVERSHOOT_CAP_MPS) useLinear[i] = 1;
  }
  return { t, x, y, sd, mx, my, useLinear };
}

function curveAt(c, t) {
  const n = c.t.length;
  if (t <= c.t[0]) return [c.x[0], c.y[0], c.sd ? c.sd[0] : null];
  if (t >= c.t[n - 1]) return [c.x[n - 1], c.y[n - 1], c.sd ? c.sd[n - 1] : null];
  const i = bsearch(c.t, t), h = c.t[i + 1] - c.t[i], s = (t - c.t[i]) / h;
  const sd = c.sd ? c.sd[i] + (c.sd[i + 1] - c.sd[i]) * s : null;
  if (c.useLinear[i]) return [c.x[i] + (c.x[i + 1] - c.x[i]) * s, c.y[i] + (c.y[i + 1] - c.y[i]) * s, sd];
  return [hermite(c.x[i], c.mx[i], c.x[i + 1], c.mx[i + 1], s, h),
          hermite(c.y[i], c.my[i], c.y[i + 1], c.my[i + 1], s, h), sd];
}

// Trapezoidal speed profile over f in [0,1]: accelerate for fraction r, cruise,
// decelerate for r. Peak speed = mean / (1 - r). r is chosen so the peak stays
// <= MAX_SPEED_MPS; r=0 is constant speed.
function rampFor(meanSpeed) { return Math.max(0, Math.min(0.25, 1 - meanSpeed / MAX_SPEED_MPS)); }
function trapezoid(f, r) {
  if (r <= 1e-6) return f;
  const k = 1 / (2 * r * (1 - r));
  if (f < r) return f * f * k;
  if (f > 1 - r) return 1 - (1 - f) * (1 - f) * k;
  return (f - r / 2) / (1 - r);
}
function smoothstep(f) { f = Math.max(0, Math.min(1, f)); return f * f * (3 - 2 * f); }

// ---- per-track preparation ---------------------------------------------------

function prepareTracks() {
  for (const side of ['home', 'away']) {
    const isHome = side === 'home';
    for (const tr of MATCH_DATA[side].tracks) {
      tr.isHome = isHome;
      tr.anchorT = tr.anchors.map(a => a[0]);
      const keepT = tr.anchors.map(a => a[0]).concat((tr.kickoff || []).map(k => k[0])).sort((a, b) => a - b);
      const isKeep = t => { const i = bsearch(keepT, t + 0.002); return i >= 0 && Math.abs(keepT[i] - t) <= 0.002; };
      tr.real = [], tr.tact = [];
      for (const s of tr.segs) {
        tr.real.push(makeCurve(s.t, s.x, s.y, s.sd));
        const idx = [];
        for (let i = 0; i < s.t.length; i++) {
          const t = s.t[i];
          if (i === 0 || i === s.t.length - 1 || isKeep(t) ||
              (Math.abs(t - Math.round(t)) < 1e-6 && Math.round(t) % TACTICAL_KNOT_S === 0)) idx.push(i);
        }
        // keep every knot inside a thinned interval that would be fast, so thinning
        // never creates speed the full reconstruction does not have
        const full = [];
        for (let k = 0; k < idx.length; k++) {
          full.push(idx[k]);
          if (k + 1 < idx.length) {
            const a = idx[k], b = idx[k + 1];
            const v = Math.hypot(s.x[b] - s.x[a], s.y[b] - s.y[a]) / (s.t[b] - s.t[a]);
            if (v > 0.6 * MAX_SPEED_MPS) for (let j = a + 1; j < b; j++) full.push(j);
          }
        }
        tr.tact.push(makeCurve(full.map(i => s.t[i]), full.map(i => s.x[i]), full.map(i => s.y[i]), null));
      }
      tr.segStart = tr.segs.map(s => s.t[0]);
      // carries: [t0, t1, x0, y0, x1, y1, anomaly]
      tr.carryObjs = (tr.carries || []).map(c => {
        const [t0, t1, x0, y0, x1, y1, anom] = c;
        const len = Math.hypot(x1 - x0, y1 - y0), dur = Math.max(t1 - t0, 1e-3);
        const mean = len / dur;
        const tEnd = anom ? t0 + len / MAX_SPEED_MPS : t1;   // capped: arrives later, logged in payload.anomalies
        const obj = { t0, t1, tEnd, x0, y0, x1, y1, len, anom: !!anom, r: anom ? 0 : rampFor(mean), ok: true };
        // an inner real anchor off the straight path would be violated: keep the reconstruction there
        for (const a of tr.anchors) {
          if (a[0] > t0 + 0.02 && a[0] < t1 - 0.02) {
            const p = carryPathAt(obj, a[0]);
            if (Math.hypot(p[0] - a[1], p[1] - a[2]) > 0.5) { obj.ok = false; break; }
          }
        }
        return obj;
      }).filter(c => c.len > 0.05);
      tr.carryT0 = tr.carryObjs.map(c => c.t0);
      tr.recObjs = (tr.receptions || []).filter(r => r.t_receive > r.t_pass + 0.05).map(r => {
        const inner = tr.anchorT.some(t => t > r.t_pass + 0.02 && t < r.t_receive - 0.02);
        return { ...r, ok: !inner };
      });
      tr.recT0 = tr.recObjs.map(r => r.t_pass);
    }
  }
}

function activeIntervals(tr) { return tr.intervals; }
function trackActiveAt(tr, t) {
  for (const [a, b] of tr.intervals) if (t >= a && t <= b) return true;
  return false;
}

function baseAt(tr, t, mode) {
  const curves = mode === 'tactical' ? tr.tact : tr.real;
  if (!curves.length) return [PITCH_LEN_M / 2, PITCH_WID_M / 2, null];
  let i = bsearch(tr.segStart, t);
  if (i < 0) i = 0;
  return curveAt(curves[i], t);
}

function carryPathAt(c, t) {
  const f = Math.max(0, Math.min(1, (t - c.t0) / (c.tEnd - c.t0)));
  const s = trapezoid(f, c.r);
  return [c.x0 + (c.x1 - c.x0) * s, c.y0 + (c.y1 - c.y0) * s];
}

function activeCarry(tr, t) {
  const i = bsearch(tr.carryT0, t);
  for (let k = i; k >= 0 && k >= i - 2; k--) {
    const c = tr.carryObjs[k];
    if (c && c.ok && t >= c.t0 && t <= c.tEnd + (c.anom ? 0.5 : 0)) return c;
  }
  return null;
}

function carryPosition(tr, t, base) {
  const c = activeCarry(tr, t);
  if (!c) return null;
  if (t <= c.tEnd) {
    // The reconstruction passes through the carry's real start (t0) and end
    // (t1) anchors, so the real carry path is position-continuous with it at
    // both edges without any blending; a blend weight measurably added speed
    // above the 9.5 m/s cap on fast real carries (CHANGELOG_fix.md, D6).
    return carryPathAt(c, t);
  }
  const w = smoothstep((t - c.tEnd) / 0.5);  // anomaly: blend back into the reconstruction
  return [c.x1 + (base[0] - c.x1) * w, c.y1 + (base[1] - c.y1) * w];
}

function anticipation(tr, t) {
  const i = bsearch(tr.recT0, t);
  for (let k = i; k >= 0 && k >= i - 2; k--) {
    const r = tr.recObjs[k];
    if (!r || !r.ok || t < r.t_pass || t > r.t_receive) continue;
    const p0 = baseAt(tr, r.t_pass, 'tactical'), p1 = baseAt(tr, r.t_receive, 'tactical');
    const dur = r.t_receive - r.t_pass, mean = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / dur;
    if (mean > MAX_SPEED_MPS) return null;
    const s = trapezoid((t - r.t_pass) / dur, rampFor(mean));
    return [p0[0] + (p1[0] - p0[0]) * s, p0[1] + (p1[1] - p0[1]) * s];
  }
  return null;
}

function playerPositionAt(tr, t) {
  const base = baseAt(tr, t, displayMode);
  const c = carryPosition(tr, t, base);
  if (c) return [c[0], c[1], displayMode === 'realism' ? base[2] : null];
  if (displayMode === 'tactical') {
    const a = anticipation(tr, t);
    if (a) return [a[0], a[1], null];
    return [base[0], base[1], null];
  }
  return base;
}

// ---- ball ---------------------------------------------------------------------

function toHomeFrame(x, y, isHome) { return isHome ? [x, y] : [PITCH_LEN_M - x, PITCH_WID_M - y]; }

function ballHomeAt(t) {
  const B = MATCH_DATA.ball, n = B.t.length;
  if (t <= B.t[0]) return [B.x[0], B.y[0]];
  if (t >= B.t[n - 1]) return [B.x[n - 1], B.y[n - 1]];
  const i = bsearch(B.t, t), f = (t - B.t[i]) / (B.t[i + 1] - B.t[i]);
  return [B.x[i] + (B.x[i + 1] - B.x[i]) * f, B.y[i] + (B.y[i + 1] - B.y[i]) * f];
}

let carrierIndex = null;
function carrierAt(t) {
  if (!carrierIndex) {
    carrierIndex = [];
    for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks)
      for (const c of tr.carryObjs) if (c.ok) carrierIndex.push([c.t0, c.tEnd, tr, c]);
    carrierIndex.sort((a, b) => a[0] - b[0]);
    carrierIndex.t0 = carrierIndex.map(c => c[0]);
  }
  const i = bsearch(carrierIndex.t0, t);
  for (let k = i; k >= 0 && k >= i - 3; k--) { const c = carrierIndex[k]; if (c && t >= c[0] && t <= c[1]) return c; }
  return null;
}

// Ball in the HOME frame: on the carrier (small fixed offset along the carry)
// during a carry, otherwise the model's own ball path (pass flight over the real
// duration, at rest between events).
function ballHomePositionAt(t) {
  const c = carrierAt(t);
  if (c) {
    const [, , tr, car] = c;
    const p = playerPositionAt(tr, t);
    const L = Math.max(car.len, 1e-6);
    const bx = p[0] + (car.x1 - car.x0) / L * BALL_OFFSET_M, by = p[1] + (car.y1 - car.y0) / L * BALL_OFFSET_M;
    return toHomeFrame(bx, by, tr.isHome);
  }
  return ballHomeAt(t);
}

function periodAt(t) {
  const P = MATCH_DATA.periods || [{ period: 1, start: 0, end: maxT }];
  let p = P[0].period;
  for (const q of P) if (t >= q.start - 1e-6) p = q.period;
  return p;
}

function displayFrame(x, y, isHome, period) {
  if (!matchEndsMode) return [x, y];
  const flip = period === 1 ? !isHome : isHome;
  return flip ? [PITCH_LEN_M - x, PITCH_WID_M - y] : [x, y];
}

function transformCoords(x, y, isHome, period) {
  const [tx, ty] = displayFrame(x, y, isHome, period);
  const marginX = 40, marginY = 35;
  const pitchW = canvas.width - 2 * marginX, pitchH = canvas.height - 2 * marginY;
  return { px: marginX + (tx / PITCH_LEN_M) * pitchW, py: marginY + (ty / PITCH_WID_M) * pitchH };
}

function drawnBallAt(t) { // for tests: ball in the displayed pitch frame (meters)
  const [x, y] = ballHomePositionAt(t);
  return { pitch: displayFrame(x, y, true, periodAt(t)) };
}

// ---- header / rendering --------------------------------------------------------

function initHeader() {
  document.title = `⚽ ${MATCH_DATA.home.name} ${MATCH_DATA.home.score}-${MATCH_DATA.away.score} ${MATCH_DATA.away.name}`;
  document.getElementById('home-name').textContent = MATCH_DATA.home.name;
  document.getElementById('away-name').textContent = MATCH_DATA.away.name;
  document.getElementById('score-home').textContent = MATCH_DATA.home.score;
  document.getElementById('score-away').textContent = MATCH_DATA.away.score;
  document.getElementById('match-meta').textContent =
    `${MATCH_DATA.stadium || ''} • ${MATCH_DATA.date || ''} • Ref: ${MATCH_DATA.referee || 'n/a'}`;
  const m = Math.floor(maxT / 60), s = Math.floor(maxT % 60);
  document.getElementById('scrub-end').textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  document.getElementById('time-slider').max = totalEvents - 1;
  document.getElementById('select-filter-team').innerHTML = `<option value="all">🏟️ Both Teams</option>
    <option value="home">${MATCH_DATA.home.name}</option><option value="away">${MATCH_DATA.away.name}</option>`;
  const names = [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks].map(t => t.name).sort();
  document.getElementById('select-filter-player').innerHTML = '<option value="all">👤 All Players</option>' +
    names.map(n => `<option value="${n}">${n}</option>`).join('');
  document.querySelectorAll('.btn-mode').forEach(b => {
    const on = b.dataset.mode === displayMode;
    b.classList.toggle('bg-sky-500', on); b.classList.toggle('text-white', on); b.classList.toggle('text-gray-400', !on);
  });
}

function metersToPixels(m) {
  const pitchW = canvas.width - 80, pitchH = canvas.height - 70;
  return m * ((pitchW / PITCH_LEN_M + pitchH / PITCH_WID_M) / 2);
}

function drawPitch(period) {
  const w = canvas.width, h = canvas.height, marginX = 40, marginY = 35;
  const pitchW = w - 2 * marginX, pitchH = h - 2 * marginY;
  ctx.fillStyle = '#10391F'; ctx.fillRect(0, 0, w, h);
  const stripeW = pitchW / 10;
  for (let i = 0; i < 10; i++) { ctx.fillStyle = (i % 2 === 0) ? '#124124' : '#0F371D'; ctx.fillRect(marginX + i * stripeW, marginY, stripeW, pitchH); }
  ctx.strokeStyle = 'rgba(255,255,255,0.75)'; ctx.lineWidth = 2.5;
  ctx.strokeRect(marginX, marginY, pitchW, pitchH);
  const halfX = marginX + pitchW / 2, centerY = marginY + pitchH / 2;
  ctx.beginPath(); ctx.moveTo(halfX, marginY); ctx.lineTo(halfX, marginY + pitchH); ctx.stroke();
  const centerRadius = (9.15 / PITCH_LEN_M) * pitchW;
  ctx.beginPath(); ctx.arc(halfX, centerY, centerRadius, 0, Math.PI * 2); ctx.stroke();
  const penDepth = (16.5 / PITCH_LEN_M) * pitchW, penHalf = (20.15 / PITCH_WID_M) * pitchH;
  ctx.strokeRect(marginX, centerY - penHalf, penDepth, penHalf * 2);
  ctx.strokeRect(marginX + pitchW - penDepth, centerY - penHalf, penDepth, penHalf * 2);
  const sixDepth = (5.5 / PITCH_LEN_M) * pitchW, sixHalf = (9.15 / PITCH_WID_M) * pitchH;
  ctx.strokeRect(marginX, centerY - sixHalf, sixDepth, sixHalf * 2);
  ctx.strokeRect(marginX + pitchW - sixDepth, centerY - sixHalf, sixDepth, sixHalf * 2);
  const goalW = (7.32 / PITCH_WID_M) * pitchH;
  ctx.fillStyle = 'rgba(255,255,255,0.15)'; ctx.strokeStyle = '#FFFFFF'; ctx.lineWidth = 3;
  ctx.fillRect(marginX - 14, centerY - goalW / 2, 14, goalW); ctx.strokeRect(marginX - 14, centerY - goalW / 2, 14, goalW);
  ctx.fillRect(marginX + pitchW, centerY - goalW / 2, 14, goalW); ctx.strokeRect(marginX + pitchW, centerY - goalW / 2, 14, goalW);
  if (matchEndsMode) {
    ctx.font = '600 11px Inter, sans-serif'; ctx.textAlign = 'center';
    const p1 = period === 1;
    ctx.fillStyle = 'rgba(148,190,229,0.4)';
    ctx.fillText(`${(p1 ? MATCH_DATA.home.name : MATCH_DATA.away.name).toUpperCase()} DEFENDING`, marginX + 110, marginY - 12);
    ctx.fillText(`${(p1 ? MATCH_DATA.away.name : MATCH_DATA.home.name).toUpperCase()} DEFENDING`, marginX + pitchW - 110, marginY - 12);
  }
}

function gapToAnchor(tr, t) {
  const A = tr.anchorT; if (!A.length) return Infinity;
  const i = bsearch(A, t);
  const a = i >= 0 ? t - A[i] : Infinity, b = i + 1 < A.length ? A[i + 1] - t : Infinity;
  return Math.min(a, b);
}

function sidePositionsAt(side, t) {
  const out = [];
  for (const tr of MATCH_DATA[side].tracks) if (trackActiveAt(tr, t)) out.push([tr, playerPositionAt(tr, t)]);
  if (!realSpacing) return out;
  const of = out.filter(([tr]) => tr.role !== 'GK');
  if (!of.length) return out;
  const cx = of.reduce((s, [, p]) => s + p[0], 0) / of.length, cy = of.reduce((s, [, p]) => s + p[1], 0) / of.length;
  return out.map(([tr, p]) => {
    if (tr.role === 'GK') return [tr, p];
    const w = Math.min(1, gapToAnchor(tr, t) / SPACING_FADE_S), k = 1 + (SPACING_K - 1) * w;
    return [tr, [cx + (p[0] - cx) * k, cy + (p[1] - cy) * k, p[2]]];
  });
}

function drawFormations(t, currEv, period) {
  for (const side of ['home', 'away']) {
    const isHome = side === 'home';
    for (const [tr, pos0] of sidePositionsAt(side, t)) {
      const [px, py, sd] = pos0;
      const pos = transformCoords(px, py, isHome, period);
      drawPlayerToken(pos.px, pos.py, tr.name, isHome ? '#7B003A' : '#E30613', isHome ? '#94BEE5' : '#FFFFFF',
        currEv.playerId === tr.id, displayMode === 'realism' ? sd : null);
    }
  }
}

function drawPlayerToken(x, y, name, mainColor, accentColor, isActive, sd) {
  const r = isActive ? 12 : 9;
  const calib = MATCH_DATA.calibration;
  if (sd !== null && sd !== undefined && calib && showUncertainty) {
    const ringR = Math.min(metersToPixels(sd * calib.k68), 55);
    ctx.beginPath(); ctx.arc(x, y, ringR, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(148,163,184,${Math.max(0.08, 0.38 - ringR / 160)})`; ctx.lineWidth = 1; ctx.stroke();
  }
  if (isActive) {
    ctx.strokeStyle = '#38BDF8'; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.fillStyle = mainColor; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = accentColor; ctx.lineWidth = 2; ctx.stroke();
  if (isActive) {
    ctx.fillStyle = 'rgba(15,23,42,0.85)';
    const textW = ctx.measureText(name).width + 10;
    ctx.fillRect(x - textW / 2, y + r + 4, textW, 14);
    ctx.fillStyle = '#F8FAFC'; ctx.font = '600 9px Inter, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(name, x, y + r + 11);
  }
}

function drawBallAndTrail(t, ev, period) {
  if (showTrails && ev && ev.endX != null && ev.type !== 'Carry' && t >= ev.t && t <= ev.t + Math.max(ev.dur, 0.3)) {
    const s = transformCoords(ev.x, ev.y, ev.isHome, period);
    const [bx, by] = ballHomePositionAt(t);
    const b = transformCoords(bx, by, true, period);
    ctx.beginPath(); ctx.moveTo(s.px, s.py); ctx.lineTo(b.px, b.py);
    ctx.strokeStyle = ev.isGoal ? '#FACC15' : ev.isShot ? '#FB923C' : ev.passOutcome === 'Complete' ? (ev.isHome ? 'rgba(148,190,229,0.85)' : 'rgba(255,255,255,0.85)') : 'rgba(239,68,68,0.7)';
    ctx.lineWidth = ev.isShot ? 3 : 2.5; ctx.stroke();
  }
  const [bx, by] = ballHomePositionAt(t);
  const b = transformCoords(bx, by, true, period);
  ctx.fillStyle = 'rgba(0,0,0,0.4)'; ctx.beginPath(); ctx.ellipse(b.px + 1, b.py + 3, 5.5, 3.3, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#FFFFFF'; ctx.beginPath(); ctx.arc(b.px, b.py, 5.5, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#0F172A'; ctx.lineWidth = 1.2; ctx.stroke();
}

function findStoppageAt(t) {
  for (const s of MATCH_DATA.stoppages || []) if (t >= s.start && t < s.end) return s;
  return null;
}

function drawStoppageLabel(label) {
  const text = '⏩ ' + label;
  ctx.font = '700 15px Inter, sans-serif';
  const boxW = ctx.measureText(text).width + 28, boxH = 32, bx = (canvas.width - boxW) / 2, by = 14;
  ctx.fillStyle = 'rgba(15,23,42,0.88)'; ctx.strokeStyle = 'rgba(250,204,21,0.9)'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.roundRect(bx, by, boxW, boxH, 8); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#FACC15'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, by + boxH / 2 + 1);
}

let runningStats = [];
function buildRunningStats() {
  runningStats = [];
  let hPass = 0, aPass = 0, hShot = 0, aShot = 0, hGoal = 0, aGoal = 0, hFoul = 0, aFoul = 0, hCard = 0, aCard = 0;
  for (const e of events) {
    if (e.type === 'Pass') { if (e.isHome) hPass++; else aPass++; }
    else if (e.isShot) { if (e.isHome) { hShot++; if (e.isGoal) hGoal++; } else { aShot++; if (e.isGoal) aGoal++; } }
    else if (e.type === 'Foul Committed') { if (e.isHome) hFoul++; else aFoul++; }
    if (e.card) { if (e.isHome) hCard++; else aCard++; }
    runningStats.push({ hPass, aPass, hShot, aShot, hGoal, aGoal, hFoul, aFoul, hCard, aCard });
  }
}

function eventMatchesFilter(ev) {
  if (filterTeam === 'home' && !ev.isHome) return false;
  if (filterTeam === 'away' && ev.isHome) return false;
  if (filterPlayer !== 'all' && ev.playerName !== filterPlayer) return false;
  if (filterType === 'Pass' && ev.type !== 'Pass') return false;
  if (filterType === 'Shot' && !ev.isShot) return false;
  if (filterType === 'Tackle' && !['Tackle', 'Interception', 'Clearance', 'Duel'].includes(ev.type)) return false;
  if (filterType === 'Foul' && !['Foul Committed', 'Bad Behaviour'].includes(ev.type)) return false;
  return true;
}
function findNextMatchingIndex(startIdx, step) {
  let idx = startIdx + step;
  while (idx >= 0 && idx < totalEvents) { if (eventMatchesFilter(events[idx])) return idx; idx += step; }
  return startIdx;
}

function currentEventAt(clock) {
  let lo = 0, hi = timeline.length - 1;
  if (clock >= events[timeline[hi]].t) lo = hi;
  else if (clock > events[timeline[0]].t) {
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (events[timeline[mid]].t <= clock) lo = mid; else hi = mid; }
  }
  return timeline[lo];
}

function render(timestamp) {
  if (!lastTimestamp) lastTimestamp = timestamp;
  const dt = (timestamp - lastTimestamp) / 1000;
  lastTimestamp = timestamp;
  if (isPlaying) {
    if (displayMode === 'tactical') {
      if (!activeStoppage) {
        const s = findStoppageAt(matchClock);
        if (s) { activeStoppage = s; activeStoppageSpeed = (s.end - matchClock) / STOPPAGE_WARP_REAL_SECONDS; }
      }
      if (activeStoppage) {
        matchClock += dt * Math.max(BASE_SPS * playbackSpeed, activeStoppageSpeed);
        if (matchClock >= activeStoppage.end) { matchClock = activeStoppage.end; activeStoppage = null; }
      } else matchClock += dt * BASE_SPS * playbackSpeed;
    } else { activeStoppage = null; matchClock += dt * BASE_SPS * playbackSpeed; }
    if (matchClock >= maxT) { matchClock = maxT; pause(); }
  }
  const newIndex = currentEventAt(matchClock);
  if (newIndex !== currentIndex) { currentIndex = newIndex; onEventChanged(); }
  const currEv = events[currentIndex] || events[0];
  const period = periodAt(matchClock);
  drawPitch(period);
  drawFormations(matchClock, currEv, period);
  drawBallAndTrail(matchClock, currEv, period);
  if (displayMode === 'tactical' && activeStoppage) drawStoppageLabel(activeStoppage.label);
  requestAnimationFrame(render);
}

function onEventChanged() {
  const ev = events[currentIndex] || events[0];
  document.getElementById('time-slider').value = currentIndex;
  const min = String(ev.minute).padStart(2, '0'), sec = String(ev.second).padStart(2, '0');
  document.getElementById('clock-display').textContent = `${min}:${sec}`;
  document.getElementById('period-display').textContent = ev.period === 1 ? '1ST HALF' : ev.period === 2 ? '2ND HALF' : `PERIOD ${ev.period}`;
  document.getElementById('action-player').textContent = ev.playerName || '—';
  document.getElementById('action-type').textContent = ev.type;
  document.getElementById('action-team-badge').style.background = ev.isHome ? '#94BEE5' : '#E30613';
  document.getElementById('action-details').textContent = `${ev.isHome ? MATCH_DATA.home.name : MATCH_DATA.away.name} • ${min}:${sec}`;
  const st = runningStats[currentIndex];
  if (st) {
    const totalP = (st.hPass + st.aPass) || 1, hPoss = Math.round((st.hPass / totalP) * 100);
    document.getElementById('stat-poss-home').textContent = `${hPoss}%`;
    document.getElementById('stat-poss-away').textContent = `${100 - hPoss}%`;
    document.getElementById('bar-poss-home').style.width = `${hPoss}%`;
    document.getElementById('bar-poss-away').style.width = `${100 - hPoss}%`;
    document.getElementById('stat-shots-home').textContent = `${st.hShot} (${st.hGoal})`;
    document.getElementById('stat-shots-away').textContent = `${st.aShot} (${st.aGoal})`;
    document.getElementById('stat-fouls-home').textContent = `${st.hFoul} (${st.hCard}🟨)`;
    document.getElementById('stat-fouls-away').textContent = `${st.aFoul} (${st.aCard}🟨)`;
  }
  renderEventFeed();
}

function renderEventFeed() {
  const el = document.getElementById('event-feed-list');
  document.getElementById('event-index-display').textContent = `${currentIndex + 1} / ${totalEvents}`;
  const start = Math.max(0, currentIndex - 4), end = Math.min(totalEvents, currentIndex + 6);
  let html = '';
  for (let i = start; i < end; i++) {
    const ev = events[i], isCurrent = i === currentIndex;
    const icon = ev.isGoal ? '⚽' : (ev.isShot ? '💥' : (ev.type === 'Pass' ? '🎯' : (ev.type.includes('Foul') ? '⚠️' : '⚡')));
    html += `<div onclick="jumpToIndex(${i})" class="cursor-pointer px-2.5 py-1.5 rounded-lg border text-xs flex items-center justify-between ${isCurrent ? 'bg-sky-950/80 border-sky-500/80 text-white' : 'bg-gray-950/50 border-gray-800/60 text-gray-300 hover:bg-gray-800/50'}">
      <div class="flex items-center gap-2 overflow-hidden"><span class="text-sm">${icon}</span>
        <div class="truncate"><span class="font-bold ${ev.isHome ? 'text-sky-300' : 'text-red-300'}">${ev.playerName || '—'}</span>
        <span class="text-[11px] text-gray-400 ml-1 font-mono">${ev.type}</span></div></div>
      <span class="font-mono text-[11px] text-gray-400 shrink-0 ml-2">${String(ev.minute).padStart(2, '0')}:${String(ev.second).padStart(2, '0')}</span></div>`;
  }
  el.innerHTML = html;
}

function play() { isPlaying = true; document.getElementById('play-icon').textContent = '⏸️'; document.getElementById('play-text').textContent = 'Pause'; }
function pause() { isPlaying = false; document.getElementById('play-icon').textContent = '▶️'; document.getElementById('play-text').textContent = 'Play'; }
function togglePlay() { if (isPlaying) pause(); else play(); }
function jumpToIndex(idx) { currentIndex = Math.max(0, Math.min(totalEvents - 1, idx)); matchClock = events[currentIndex].t; onEventChanged(); }

function wireControls() {
  document.getElementById('btn-play').addEventListener('click', togglePlay);
  document.getElementById('btn-prev').addEventListener('click', () => jumpToIndex(findNextMatchingIndex(currentIndex, -1)));
  document.getElementById('btn-next').addEventListener('click', () => jumpToIndex(findNextMatchingIndex(currentIndex, 1)));
  document.getElementById('btn-restart').addEventListener('click', () => { jumpToIndex(0); play(); });
  document.getElementById('time-slider').addEventListener('input', e => jumpToIndex(parseInt(e.target.value)));
  document.querySelectorAll('.btn-speed').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.btn-speed').forEach(b => { b.classList.remove('bg-sky-500', 'text-white'); b.classList.add('text-gray-400'); });
    btn.classList.add('bg-sky-500', 'text-white'); btn.classList.remove('text-gray-400');
    playbackSpeed = parseFloat(btn.dataset.speed);
  }));
  document.querySelectorAll('.btn-mode').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.btn-mode').forEach(b => { b.classList.remove('bg-sky-500', 'text-white'); b.classList.add('text-gray-400'); });
    btn.classList.add('bg-sky-500', 'text-white'); btn.classList.remove('text-gray-400');
    displayMode = btn.dataset.mode; activeStoppage = null;
  }));
  document.getElementById('select-filter-team').addEventListener('change', e => { filterTeam = e.target.value; if (!eventMatchesFilter(events[currentIndex])) jumpToIndex(findNextMatchingIndex(currentIndex, 1)); });
  document.getElementById('select-filter-type').addEventListener('change', e => { filterType = e.target.value; if (!eventMatchesFilter(events[currentIndex])) jumpToIndex(findNextMatchingIndex(currentIndex, 1)); });
  document.getElementById('select-filter-player').addEventListener('change', e => { filterPlayer = e.target.value; if (!eventMatchesFilter(events[currentIndex])) jumpToIndex(findNextMatchingIndex(currentIndex, 1)); });
  document.getElementById('btn-pitch-mode').addEventListener('click', () => { matchEndsMode = !matchEndsMode; document.getElementById('pitch-mode-label').textContent = matchEndsMode ? 'Match Ends' : 'Attack L->R'; });
  document.getElementById('btn-trails').addEventListener('click', e => { showTrails = !showTrails; e.currentTarget.querySelector('span:last-child').textContent = `Trails: ${showTrails ? 'ON' : 'OFF'}`; });
  const sp = document.getElementById('btn-spacing');
  if (sp) sp.addEventListener('click', e => { realSpacing = !realSpacing; e.currentTarget.querySelector('span:last-child').textContent = `Spacing: ${realSpacing ? 'real-scaled (display only)' : 'model'}`; });
  document.getElementById('btn-uncertainty').addEventListener('click', e => { showUncertainty = !showUncertainty; e.currentTarget.querySelector('span:last-child').textContent = `Uncertainty: ${showUncertainty ? 'ON' : 'OFF'}`; });
  window.addEventListener('keydown', e => {
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    else if (e.code === 'ArrowRight') { e.preventDefault(); jumpToIndex(findNextMatchingIndex(currentIndex, 1)); }
    else if (e.code === 'ArrowLeft') { e.preventDefault(); jumpToIndex(findNextMatchingIndex(currentIndex, -1)); }
  });
}
window.jumpToIndex = jumpToIndex;
boot();
