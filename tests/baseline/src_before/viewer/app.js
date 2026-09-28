// Generic match viewer: loads ONE match's payload (built by
// pipeline/build_payload.py) via fetch and renders it. No match-specific
// data is hardcoded here — every team name, score, player, and stoppage
// comes from the fetched JSON. See tracking_model equivalent docs in
// BENCHMARKS.md for what the "Full Realism" vs "Tactical Clarity" modes
// mean and what is/isn't a validated accuracy claim.

const params = new URLSearchParams(location.search);
const MATCH_ID = params.get('match') || '3754348';

let MATCH_DATA = null;
let canvas, ctx;
let currentIndex = 0, isPlaying = false, playbackSpeed = 1.0, animFrameId = null, lastTimestamp = 0;
let showTrails = true, showUncertainty = true, matchEndsMode = true;
let filterTeam = 'all', filterType = 'all', filterPlayer = 'all';
let displayMode = 'realism';
let activeStoppage = null, activeStoppageSpeed = 0;
let matchClock = 0;
let events = [], timeline = [], totalEvents = 0, maxT = 6000;
let PITCH_LEN_M = 105, PITCH_WID_M = 68;
const BASE_SPS = 1.0;
const MAX_SPEED_MPS = 8.0;
const STOPPAGE_WARP_REAL_SECONDS = 1.5;

async function boot() {
  canvas = document.getElementById('pitchCanvas');
  ctx = canvas.getContext('2d');
  const res = await fetch(`data/${MATCH_ID}.json`);
  if (!res.ok) {
    document.getElementById('load-error').textContent = `Could not load data/${MATCH_ID}.json (${res.status})`;
    document.getElementById('load-error').classList.remove('hidden');
    return;
  }
  MATCH_DATA = await res.json();
  PITCH_LEN_M = MATCH_DATA.pitchLengthM || 105;
  PITCH_WID_M = MATCH_DATA.pitchWidthM || 68;
  events = MATCH_DATA.events;
  totalEvents = events.length;
  maxT = MATCH_DATA.maxT || (events.length ? events[events.length - 1].t : 6000);
  timeline = events.map((e, i) => i).sort((a, b) => events[a].t - events[b].t);

  initHeader();
  precomputeTangents();
  buildRunningStats();
  wireControls();
  onEventChanged();
  animFrameId = requestAnimationFrame(render);
}

function initHeader() {
  document.title = `⚽ ${MATCH_DATA.home.name} ${MATCH_DATA.home.score}-${MATCH_DATA.away.score} ${MATCH_DATA.away.name}`;
  document.getElementById('home-name').textContent = MATCH_DATA.home.name;
  document.getElementById('away-name').textContent = MATCH_DATA.away.name;
  document.getElementById('score-home').textContent = MATCH_DATA.home.score;
  document.getElementById('score-away').textContent = MATCH_DATA.away.score;
  document.getElementById('match-meta').textContent =
    `${MATCH_DATA.stadium || ''} • ${MATCH_DATA.date || ''} • Ref: ${MATCH_DATA.referee || 'n/a'}`;
  const scrubEnd = document.getElementById('scrub-end');
  const m = Math.floor(maxT / 60), s = Math.floor(maxT % 60);
  scrubEnd.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  document.getElementById('time-slider').max = totalEvents - 1;

  const teamSel = document.getElementById('select-filter-team');
  teamSel.innerHTML = `<option value="all">🏟️ Both Teams</option>
    <option value="home">${MATCH_DATA.home.name}</option>
    <option value="away">${MATCH_DATA.away.name}</option>`;

  const playerSel = document.getElementById('select-filter-player');
  const names = [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks].map(t => t.name).sort();
  playerSel.innerHTML = '<option value="all">👤 All Players</option>' +
    names.map(n => `<option value="${n}">${n}</option>`).join('');
}

// ---- Geometry / interpolation (generic, data-driven) ----------------------

function gapDistanceMeters(dx, dy) { return Math.hypot(dx, dy); } // tracks are already in meters

function pchipTangents(waypoints, axis) {
  const n = waypoints.length;
  const m = new Array(n).fill(0);
  if (n < 2) return m;
  const h = new Array(n - 1), d = new Array(n - 1);
  for (let i = 0; i < n - 1; i++) {
    h[i] = waypoints[i + 1][0] - waypoints[i][0];
    d[i] = h[i] > 0 ? (waypoints[i + 1][axis] - waypoints[i][axis]) / h[i] : 0;
  }
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] === 0 || d[i] === 0 || (d[i - 1] > 0) !== (d[i] > 0)) m[i] = 0;
    else { const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1]; m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]); }
  }
  return m;
}

function pchipTangentsArr(t, v) {
  const n = t.length;
  const m = new Array(n).fill(0);
  if (n < 2) return m;
  const h = new Array(n - 1), d = new Array(n - 1);
  for (let i = 0; i < n - 1; i++) {
    h[i] = t[i + 1] - t[i];
    d[i] = h[i] > 0 ? (v[i + 1] - v[i]) / h[i] : 0;
  }
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] === 0 || d[i] === 0 || (d[i - 1] > 0) !== (d[i] > 0)) m[i] = 0;
    else { const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1]; m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]); }
  }
  return m;
}

function hermiteEval(p0, m0, p1, m1, s, h) {
  const s2 = s * s, s3 = s2 * s;
  const h00 = 2 * s3 - 3 * s2 + 1, h10 = s3 - 2 * s2 + s, h01 = -2 * s3 + 3 * s2, h11 = s3 - s2;
  return h00 * p0 + h10 * h * m0 + h01 * p1 + h11 * h * m1;
}

function precomputeTangents() {
  [...MATCH_DATA.home.tracks, ...MATCH_DATA.away.tracks].forEach(track => {
    const mx = pchipTangents(track.waypoints, 1);
    const my = pchipTangents(track.waypoints, 2);
    const PEAK_MPS = 9.5;
    for (let i = 0; i < mx.length; i++) {
      const spd = gapDistanceMeters(mx[i], my[i]);
      if (spd > PEAK_MPS) { const s = PEAK_MPS / spd; mx[i] *= s; my[i] *= s; }
    }
    track._mx = mx; track._my = my;
  });
}

function wanderOffsetM(seed, t) {
  const wx = Math.sin(t * 0.7 + seed) * 0.6 + Math.sin(t * 0.23 + seed * 1.7) * 0.4;
  const wy = Math.cos(t * 0.55 + seed * 2.1) * 0.6 + Math.cos(t * 0.31 + seed * 0.9) * 0.4;
  return [wx, wy];
}

function trackPositionAt(track, t, wander) {
  if (wander === undefined) wander = true;
  const waypoints = track.waypoints;
  const n = waypoints.length;
  if (t <= waypoints[0][0]) return [waypoints[0][1], waypoints[0][2]];
  if (t >= waypoints[n - 1][0]) return [waypoints[n - 1][1], waypoints[n - 1][2]];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (waypoints[mid][0] <= t) lo = mid; else hi = mid; }
  const [t0, x0, y0] = waypoints[lo];
  const [t1, x1, y1] = waypoints[hi];
  const dt = Math.max(0.001, t1 - t0);
  const f = (t - t0) / dt;
  const neededSpeed = gapDistanceMeters(x1 - x0, y1 - y0) / dt;
  let px, py;
  if (neededSpeed > MAX_SPEED_MPS) {
    const ef = Math.min(1, f * 1.6);
    px = x0 + (x1 - x0) * ef; py = y0 + (y1 - y0) * ef;
  } else {
    px = hermiteEval(x0, track._mx[lo], x1, track._mx[hi], f, dt);
    py = hermiteEval(y0, track._my[lo], y1, track._my[hi], f, dt);
  }
  if (wander && dt > 8) {
    const edgeFade = Math.min(1, Math.min(t - t0, t1 - t) / 3);
    const wanderAmpM = Math.min(1.4, dt / 40) * edgeFade;
    if (wanderAmpM > 0.01) {
      const [wxM, wyM] = wanderOffsetM(track.id * 0.0137, t);
      px += wxM * wanderAmpM; py += wyM * wanderAmpM;
    }
  }
  px = Math.max(-5, Math.min(PITCH_LEN_M + 5, px));
  py = Math.max(-5, Math.min(PITCH_WID_M + 5, py));
  return [px, py];
}

function trackActiveAt(track, t) {
  if (track.enter !== null && t < track.enter) return false;
  if (track.exit !== null && t > track.exit) return false;
  return true;
}

function smoothedPositionAt(track, t) {
  const s = track.smoothed;
  if (!s || !s.t || s.t.length === 0) return null;
  const arr = s.t, n = arr.length;
  if (t <= arr[0]) return [s.x[0], s.y[0], s.sd[0]];
  if (t >= arr[n - 1]) return [s.x[n - 1], s.y[n - 1], s.sd[n - 1]];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (arr[mid] <= t) lo = mid; else hi = mid; }
  if (!s._mx) {
    const mx = pchipTangentsArr(arr, s.x), my = pchipTangentsArr(arr, s.y);
    const PEAK_MPS = 9.5;
    for (let i = 0; i < mx.length; i++) {
      const spd = gapDistanceMeters(mx[i], my[i]);
      if (spd > PEAK_MPS) { const sc = PEAK_MPS / spd; mx[i] *= sc; my[i] *= sc; }
    }
    const velAt = (p0, m0, p1, m1, hh, ss) => {
      const s2 = ss * ss;
      const d00 = 6 * s2 - 6 * ss, d10 = 3 * s2 - 4 * ss + 1, d01 = -6 * s2 + 6 * ss, d11 = 3 * s2 - 2 * ss;
      return (d00 * p0 + d10 * hh * m0 + d01 * p1 + d11 * hh * m1) / hh;
    };
    const useLinear = new Uint8Array(Math.max(0, mx.length - 1));
    for (let i = 0; i < mx.length - 1; i++) {
      const hh = arr[i + 1] - arr[i];
      if (hh <= 0) continue;
      let peak = 0;
      for (const ss of [0.2, 0.4, 0.5, 0.6, 0.8]) {
        const vx = velAt(s.x[i], mx[i], s.x[i + 1], mx[i + 1], hh, ss);
        const vy = velAt(s.y[i], my[i], s.y[i + 1], my[i + 1], hh, ss);
        if (gapDistanceMeters(vx, vy) > peak) peak = gapDistanceMeters(vx, vy);
      }
      if (peak > PEAK_MPS * 1.15) useLinear[i] = 1;
    }
    s._mx = mx; s._my = my; s._useLinear = useLinear;
  }
  const h = arr[hi] - arr[lo];
  const f = h > 0 ? (t - arr[lo]) / h : 0;
  let x, y;
  if (s._useLinear[lo]) { x = s.x[lo] + (s.x[hi] - s.x[lo]) * f; y = s.y[lo] + (s.y[hi] - s.y[lo]) * f; }
  else { x = hermiteEval(s.x[lo], s._mx[lo], s.x[hi], s._mx[hi], f, h); y = hermiteEval(s.y[lo], s._my[lo], s.y[hi], s._my[hi], f, h); }
  const sd = s.sd[lo] + (s.sd[hi] - s.sd[lo]) * f;
  return [x, y, sd];
}

function easeInOutCubic(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
function easeOutCubic(x) { return 1 - Math.pow(1 - x, 3); }

// Tactical Clarity: presentation-only restyling on the SAME anchors (real
// touches + real pass receptions) — see BENCHMARKS.md. Off-ball = the plain
// touch spline with wander OFF (no injected noise); during a pass's
// [t_pass, t_receive] window, an explicit ease-in-out run to the real
// pass-end coordinate, synchronized with the ball's own flight animation.
function tacticalPositionAt(track, t) {
  const recs = track.receptions;
  if (recs && recs.length) {
    for (let i = 0; i < recs.length; i++) {
      const r = recs[i];
      if (t < r.t_pass || t > r.t_receive) continue;
      const [px0, py0] = trackPositionAt(track, r.t_pass, false);
      const span = Math.max(0.001, r.t_receive - r.t_pass);
      const ease = easeInOutCubic(Math.max(0, Math.min(1, (t - r.t_pass) / span)));
      return [px0 + (r.end_x - px0) * ease, py0 + (r.end_y - py0) * ease, null];
    }
  }
  const [px, py] = trackPositionAt(track, t, false);
  return [px, py, null];
}

function playerPositionAt(track, t) {
  if (displayMode === 'tactical') return tacticalPositionAt(track, t);
  const sm = smoothedPositionAt(track, t);
  if (sm) return sm;
  const [px, py] = trackPositionAt(track, t);
  return [px, py, null];
}

// ---- Stoppages (Tactical Clarity only) -------------------------------------

const STOPPAGE_WARP_S = STOPPAGE_WARP_REAL_SECONDS;
function findStoppageAt(t) {
  const S = MATCH_DATA.stoppages || [];
  for (let i = 0; i < S.length; i++) if (t >= S[i].start && t < S[i].end) return S[i];
  return null;
}

// ---- Rendering --------------------------------------------------------------

function transformCoords(x, y, isHome, period) {
  let tx = x, ty = y;
  if (matchEndsMode) {
    if (period === 1) { if (!isHome) { tx = PITCH_LEN_M - x; ty = PITCH_WID_M - y; } }
    else { if (isHome) { tx = PITCH_LEN_M - x; ty = PITCH_WID_M - y; } }
  }
  const marginX = 40, marginY = 35;
  const pitchW = canvas.width - 2 * marginX, pitchH = canvas.height - 2 * marginY;
  return { px: marginX + (tx / PITCH_LEN_M) * pitchW, py: marginY + (ty / PITCH_WID_M) * pitchH };
}

function metersToPixels(m) {
  const marginX = 40, marginY = 35;
  const pitchW = canvas.width - 2 * marginX, pitchH = canvas.height - 2 * marginY;
  return m * ((pitchW / PITCH_LEN_M + pitchH / PITCH_WID_M) / 2);
}

function drawPitch() {
  const w = canvas.width, h = canvas.height, marginX = 40, marginY = 35;
  const pitchW = w - 2 * marginX, pitchH = h - 2 * marginY;
  ctx.fillStyle = '#10391F'; ctx.fillRect(0, 0, w, h);
  const stripeCount = 10, stripeW = pitchW / stripeCount;
  for (let i = 0; i < stripeCount; i++) { ctx.fillStyle = (i % 2 === 0) ? '#124124' : '#0F371D'; ctx.fillRect(marginX + i * stripeW, marginY, stripeW, pitchH); }
  ctx.strokeStyle = 'rgba(255,255,255,0.75)'; ctx.lineWidth = 2.5;
  ctx.strokeRect(marginX, marginY, pitchW, pitchH);
  const halfX = marginX + pitchW / 2;
  ctx.beginPath(); ctx.moveTo(halfX, marginY); ctx.lineTo(halfX, marginY + pitchH); ctx.stroke();
  const centerRadius = (9.15 / PITCH_LEN_M) * pitchW;
  ctx.beginPath(); ctx.arc(halfX, marginY + pitchH / 2, centerRadius, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = 'rgba(255,255,255,0.9)'; ctx.beginPath(); ctx.arc(halfX, marginY + pitchH / 2, 3.5, 0, Math.PI * 2); ctx.fill();
  const penDepth = (16.5 / PITCH_LEN_M) * pitchW, penHalfWidth = (20.15 / PITCH_WID_M) * pitchH, centerY = marginY + pitchH / 2;
  ctx.strokeRect(marginX, centerY - penHalfWidth, penDepth, penHalfWidth * 2);
  ctx.strokeRect(marginX + pitchW - penDepth, centerY - penHalfWidth, penDepth, penHalfWidth * 2);
  const sixDepth = (5.5 / PITCH_LEN_M) * pitchW, sixHalfWidth = (9.15 / PITCH_WID_M) * pitchH;
  ctx.strokeRect(marginX, centerY - sixHalfWidth, sixDepth, sixHalfWidth * 2);
  ctx.strokeRect(marginX + pitchW - sixDepth, centerY - sixHalfWidth, sixDepth, sixHalfWidth * 2);
  const penSpotDist = (11 / PITCH_LEN_M) * pitchW;
  ctx.beginPath(); ctx.arc(marginX + penSpotDist, centerY, 3, 0, Math.PI * 2); ctx.arc(marginX + pitchW - penSpotDist, centerY, 3, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(marginX + penSpotDist, centerY, centerRadius, -0.65, 0.65); ctx.stroke();
  ctx.beginPath(); ctx.arc(marginX + pitchW - penSpotDist, centerY, centerRadius, Math.PI - 0.65, Math.PI + 0.65); ctx.stroke();
  const cornerR = (1 / PITCH_LEN_M) * pitchW * 2.5;
  [[marginX, marginY, 0, Math.PI / 2], [marginX + pitchW, marginY, Math.PI / 2, Math.PI],
   [marginX, marginY + pitchH, -Math.PI / 2, 0], [marginX + pitchW, marginY + pitchH, Math.PI, Math.PI * 1.5]]
    .forEach(([cx, cy, sa, ea]) => { ctx.beginPath(); ctx.arc(cx, cy, cornerR, sa, ea); ctx.stroke(); });
  const goalWidth = (7.32 / PITCH_WID_M) * pitchH, goalDepth = 14;
  ctx.fillStyle = 'rgba(255,255,255,0.15)'; ctx.strokeStyle = '#FFFFFF'; ctx.lineWidth = 3;
  ctx.fillRect(marginX - goalDepth, centerY - goalWidth / 2, goalDepth, goalWidth);
  ctx.strokeRect(marginX - goalDepth, centerY - goalWidth / 2, goalDepth, goalWidth);
  ctx.fillRect(marginX + pitchW, centerY - goalWidth / 2, goalDepth, goalWidth);
  ctx.strokeRect(marginX + pitchW, centerY - goalWidth / 2, goalDepth, goalWidth);
  ctx.font = '600 11px Inter, sans-serif'; ctx.textAlign = 'center';
  if (matchEndsMode) {
    const isPeriod1 = (events[currentIndex]?.period || 1) === 1;
    ctx.fillStyle = isPeriod1 ? 'rgba(148,190,229,0.4)' : 'rgba(227,6,19,0.4)';
    ctx.fillText(isPeriod1 ? `${MATCH_DATA.home.name.toUpperCase()} DEFENDING` : `${MATCH_DATA.away.name.toUpperCase()} DEFENDING`, marginX + 110, marginY - 12);
    ctx.fillStyle = isPeriod1 ? 'rgba(227,6,19,0.4)' : 'rgba(148,190,229,0.4)';
    ctx.fillText(isPeriod1 ? `${MATCH_DATA.away.name.toUpperCase()} DEFENDING` : `${MATCH_DATA.home.name.toUpperCase()} DEFENDING`, marginX + pitchW - 110, marginY - 12);
  }
}

function drawFormations(t, currEv) {
  const period = currEv.period || 1;
  MATCH_DATA.home.tracks.forEach(track => {
    if (!trackActiveAt(track, t)) return;
    const [px, py, sd] = playerPositionAt(track, t);
    const pos = transformCoords(px, py, true, period);
    drawPlayerToken(pos.px, pos.py, track.name, '#7B003A', '#94BEE5', currEv.playerId === track.id, sd);
  });
  MATCH_DATA.away.tracks.forEach(track => {
    if (!trackActiveAt(track, t)) return;
    const [px, py, sd] = playerPositionAt(track, t);
    const pos = transformCoords(px, py, false, period);
    drawPlayerToken(pos.px, pos.py, track.name, '#E30613', '#FFFFFF', currEv.playerId === track.id, sd);
  });
}

function drawPlayerToken(x, y, name, mainColor, accentColor, isActive, sd) {
  const r = isActive ? 12 : 9;
  const calib = MATCH_DATA.calibration;
  if (sd !== null && sd !== undefined && calib && showUncertainty) {
    const rawR = metersToPixels(sd * calib.k68);
    const ringR = Math.min(rawR, 55);
    const alpha = Math.max(0.08, 0.38 - ringR / 160);
    ctx.beginPath(); ctx.arc(x, y, ringR, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(148,163,184,${alpha})`; ctx.lineWidth = 1; ctx.stroke();
  }
  if (isActive) {
    ctx.strokeStyle = '#38BDF8'; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = 'rgba(56,189,248,0.2)'; ctx.beginPath(); ctx.arc(x, y, r + 8, 0, Math.PI * 2); ctx.fill();
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

function drawEventAction(ev, progress) {
  const period = ev.period || 1;
  const start = transformCoords(ev.x ?? PITCH_LEN_M / 2, ev.y ?? PITCH_WID_M / 2, ev.isHome, period);
  let ballX = start.px, ballY = start.py;
  let end = null;
  if (ev.endX != null && ev.endY != null) end = transformCoords(ev.endX, ev.endY, ev.isHome, period);
  if (end) {
    const ease = easeOutCubic(progress);
    ballX = start.px + (end.px - start.px) * ease; ballY = start.py + (end.py - start.py) * ease;
    if (showTrails) {
      ctx.beginPath(); ctx.moveTo(start.px, start.py); ctx.lineTo(ballX, ballY);
      if (ev.isGoal) { ctx.strokeStyle = '#FACC15'; ctx.lineWidth = 4; ctx.shadowColor = '#EAB308'; ctx.shadowBlur = 15; }
      else if (ev.isShot) { ctx.strokeStyle = '#FB923C'; ctx.lineWidth = 3; ctx.shadowColor = '#F97316'; ctx.shadowBlur = 10; }
      else if (ev.passOutcome === 'Complete' || (ev.type !== 'Pass' && !ev.isShot)) { ctx.strokeStyle = ev.isHome ? 'rgba(148,190,229,0.85)' : 'rgba(255,255,255,0.85)'; ctx.lineWidth = 2.5; ctx.shadowBlur = 0; }
      else { ctx.strokeStyle = 'rgba(239,68,68,0.7)'; ctx.lineWidth = 2; ctx.setLineDash([4, 4]); ctx.shadowBlur = 0; }
      ctx.stroke(); ctx.setLineDash([]); ctx.shadowBlur = 0;
    }
    ctx.strokeStyle = (ev.passOutcome === 'Complete' || ev.isGoal) ? 'rgba(74,222,128,0.6)' : 'rgba(248,113,113,0.6)';
    ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(end.px, end.py, 4, 0, Math.PI * 2); ctx.stroke();
  }
  if (['Tackle', 'Interception', 'Duel'].includes(ev.type)) {
    const waveR = 8 + progress * 20;
    ctx.strokeStyle = `rgba(56,189,248,${1.0 - progress})`; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(start.px, start.py, waveR, 0, Math.PI * 2); ctx.stroke();
  }
  const ballR = 5.5;
  ctx.fillStyle = 'rgba(0,0,0,0.4)'; ctx.beginPath(); ctx.ellipse(ballX + 1, ballY + 3, ballR, ballR * 0.6, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#FFFFFF'; ctx.beginPath(); ctx.arc(ballX, ballY, ballR, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#0F172A'; ctx.lineWidth = 1.2; ctx.stroke();
  ctx.fillStyle = '#0F172A'; ctx.beginPath(); ctx.arc(ballX - 1, ballY - 1, 1.8, 0, Math.PI * 2); ctx.fill();
}

function drawStoppageLabel(label) {
  const text = '⏩ ' + label;
  ctx.font = '700 15px Inter, sans-serif';
  const textW = ctx.measureText(text).width;
  const boxW = textW + 28, boxH = 32, bx = (canvas.width - boxW) / 2, by = 14;
  ctx.fillStyle = 'rgba(15,23,42,0.88)'; ctx.strokeStyle = 'rgba(250,204,21,0.9)'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.roundRect(bx, by, boxW, boxH, 8); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#FACC15'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, by + boxH / 2 + 1);
}

// ---- Stats / feed -----------------------------------------------------------

let runningStats = [];
function buildRunningStats() {
  runningStats = [];
  let hPass = 0, aPass = 0, hPassAcc = 0, aPassAcc = 0, hShot = 0, aShot = 0, hShotTgt = 0, aShotTgt = 0,
      hFoul = 0, aFoul = 0, hCard = 0, aCard = 0;
  for (const e of events) {
    if (e.type === 'Pass') {
      if (e.isHome) { hPass++; if (e.passOutcome === 'Complete') hPassAcc++; } else { aPass++; if (e.passOutcome === 'Complete') aPassAcc++; }
    } else if (e.isShot) {
      if (e.isHome) { hShot++; if (e.isGoal) hShotTgt++; } else { aShot++; if (e.isGoal) aShotTgt++; }
    } else if (e.type === 'Foul Committed') { if (e.isHome) hFoul++; else aFoul++; }
    if (e.card) { if (e.isHome) hCard++; else aCard++; }
    runningStats.push({ hPass, aPass, hPassAcc, aPassAcc, hShot, aShot, hShotTgt, aShotTgt, hFoul, aFoul, hCard, aCard });
  }
}

function eventMatchesFilter(ev) {
  if (filterTeam === 'home' && !ev.isHome) return false;
  if (filterTeam === 'away' && ev.isHome) return false;
  if (filterPlayer !== 'all' && ev.playerName !== filterPlayer) return false;
  if (filterType !== 'all') {
    if (filterType === 'Pass' && ev.type !== 'Pass') return false;
    if (filterType === 'Shot' && !ev.isShot) return false;
    if (filterType === 'Tackle' && !['Tackle', 'Interception', 'Clearance', 'Duel'].includes(ev.type)) return false;
    if (filterType === 'Foul' && !['Foul Committed', 'Bad Behaviour'].includes(ev.type)) return false;
  }
  return true;
}
function findNextMatchingIndex(startIdx, step) {
  let idx = startIdx + step;
  while (idx >= 0 && idx < totalEvents) { if (eventMatchesFilter(events[idx])) return idx; idx += step; }
  return startIdx;
}

function render(timestamp) {
  if (!lastTimestamp) lastTimestamp = timestamp;
  const dt = (timestamp - lastTimestamp) / 1000;
  lastTimestamp = timestamp;

  if (isPlaying) {
    if (displayMode === 'tactical') {
      if (!activeStoppage) {
        const s = findStoppageAt(matchClock);
        if (s) { activeStoppage = s; activeStoppageSpeed = (s.end - matchClock) / STOPPAGE_WARP_S; }
      }
      if (activeStoppage) {
        matchClock += dt * Math.max(BASE_SPS * playbackSpeed, activeStoppageSpeed);
        if (matchClock >= activeStoppage.end) { matchClock = activeStoppage.end; activeStoppage = null; }
      } else matchClock += dt * BASE_SPS * playbackSpeed;
    } else { activeStoppage = null; matchClock += dt * BASE_SPS * playbackSpeed; }
    if (matchClock >= maxT) { matchClock = maxT; pause(); }
  }

  let lo = 0, hi = timeline.length - 1;
  if (matchClock >= events[timeline[hi]].t) lo = hi;
  else if (matchClock > events[timeline[0]].t) {
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (events[timeline[mid]].t <= matchClock) lo = mid; else hi = mid; }
  }
  const newIndex = timeline[lo];
  if (newIndex !== currentIndex) { currentIndex = newIndex; onEventChanged(); }
  const currEv = events[currentIndex] || events[0];
  const nextT = (lo + 1 < timeline.length) ? events[timeline[lo + 1]].t : currEv.t + 1;
  let localProgress = nextT > currEv.t ? (matchClock - currEv.t) / (nextT - currEv.t) : 1;
  localProgress = Math.max(0, Math.min(1, localProgress));

  drawPitch();
  drawFormations(matchClock, currEv);
  drawEventAction(currEv, localProgress);
  if (displayMode === 'tactical' && activeStoppage) drawStoppageLabel(activeStoppage.label);

  animFrameId = requestAnimationFrame(render);
}

function onEventChanged() {
  const ev = events[currentIndex] || events[0];
  document.getElementById('time-slider').value = currentIndex;
  const min = String(ev.minute).padStart(2, '0'), sec = String(ev.second).padStart(2, '0');
  document.getElementById('clock-display').textContent = `${min}:${sec}`;
  document.getElementById('period-display').textContent = ev.period === 1 ? '1ST HALF' : '2ND HALF';
  document.getElementById('action-player').textContent = ev.playerName || '—';
  document.getElementById('action-type').textContent = ev.type;
  document.getElementById('action-team-badge').style.background = ev.isHome ? '#94BEE5' : '#E30613';
  document.getElementById('action-details').textContent = `${ev.isHome ? MATCH_DATA.home.name : MATCH_DATA.away.name} • ${min}:${sec}`;

  const st = runningStats[currentIndex];
  if (st) {
    const totalP = (st.hPass + st.aPass) || 1;
    const hPoss = Math.round((st.hPass / totalP) * 100);
    document.getElementById('stat-poss-home').textContent = `${hPoss}%`;
    document.getElementById('stat-poss-away').textContent = `${100 - hPoss}%`;
    document.getElementById('bar-poss-home').style.width = `${hPoss}%`;
    document.getElementById('bar-poss-away').style.width = `${100 - hPoss}%`;
    document.getElementById('stat-shots-home').textContent = `${st.hShot} (${st.hShotTgt})`;
    document.getElementById('stat-shots-away').textContent = `${st.aShot} (${st.aShotTgt})`;
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
    html += `<div onclick="jumpToIndex(${i})" class="cursor-pointer px-2.5 py-1.5 rounded-lg border text-xs flex items-center justify-between transition ${isCurrent ? 'bg-sky-950/80 border-sky-500/80 text-white shadow-md' : 'bg-gray-950/50 border-gray-800/60 text-gray-300 hover:bg-gray-800/50'}">
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
  document.getElementById('btn-uncertainty').addEventListener('click', e => { showUncertainty = !showUncertainty; e.currentTarget.querySelector('span:last-child').textContent = `Uncertainty: ${showUncertainty ? 'ON' : 'OFF'}`; });
  window.addEventListener('keydown', e => {
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    else if (e.code === 'ArrowRight') { e.preventDefault(); jumpToIndex(findNextMatchingIndex(currentIndex, 1)); }
    else if (e.code === 'ArrowLeft') { e.preventDefault(); jumpToIndex(findNextMatchingIndex(currentIndex, -1)); }
  });
}
window.jumpToIndex = jumpToIndex;
boot();
