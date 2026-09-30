// Match viewer. Loads output/data/<match>.js (pipeline/build_payload.py).
//
// Both display modes draw the SAME smoothed reconstruction (per-player segments
// on a 1 s grid plus every real anchor time, so real anchors are knots of the
// displayed path). Differences:
//   Full Realism     all knots, uncertainty rings.
//   Tactical Clarity non-anchor knots thinned to every 3 s (less wobble; every
//                    anchor kept), pass anticipation, no rings.
// In both modes a Carry is drawn from its real start to its real end.
//
// Everything drawn is a pure function of the match clock (positions, ball,
// banners, overlays). Pacing only decides how fast the match clock advances per
// second of playback (see "pacing"): it slows down wherever something on screen
// would otherwise move faster than real players/balls do, speeds up sustained
// idle play a little, and skips the dead time of stoppages. It never reorders
// events or moves a real location, and the clock is exact at every real event.

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
let matchClock = 0;
let events = [], timeline = [], totalEvents = 0, maxT = 6000;
let PITCH_LEN_M = 105, PITCH_WID_M = 68;
const MAX_SPEED_MPS = 9.5;       // reconstruction curve limits (tangent clamp), unchanged
const TACTICAL_KNOT_S = 3;
const BALL_OFFSET_M = 0.7;
const HARD_KINDS = new Set([0, 1, 2, 3]);
// On-screen speed caps, from real tracking (tests/analysis/speed_caps.json:
// Metrica + DFL/IDSSE, 1 s windows): running p99.9 = 7.9 m/s, carrying p99 =
// 7.5 m/s (p99.9 8.5 rests on ~50 windows and includes chasing a loose ball),
// ball p99.9 = 30.6 m/s.
const RUN_CAP_MPS = 8.0, CARRY_CAP_MPS = 7.5, BALL_CAP_MPS = 30.0;
// Idle play may be sped up (at most COMPRESS_MAX) only while every player
// stays under PLAYER_COMFORT_MPS (real p90 = 3.8) and the ball under
// BALL_COMFORT_MPS (real p90 = 12.0) for SLACK_DILATE_S either side.
const PLAYER_COMFORT_MPS = 4.0, BALL_COMFORT_MPS = 12.0, COMPRESS_MAX = 1.5;
const PACE_BIN_S = 0.05, PACE_CHUNK_S = 15;
const STRETCH_DILATE_S = 0.5, STRETCH_SIGMA_S = 0.15, SLACK_DILATE_S = 2.0, SLACK_SIGMA_S = 0.6;
const STRETCH_MAX = 12;          // beyond this a data error is retimed instead (logged)
// Stoppages: the real play into the stoppage is shown, then the dead time is
// skipped in SKIP_PLAYBACK_S, then the restart set-up is shown.
const HOLD_OUT_S = 1.0, LEAD_IN_S = 1.2, SKIP_MIN_S = 3.0, SKIP_PLAYBACK_S = 1.6;
const BANNER_FADE_S = 0.4, BANNER_AFTER_S = 1.6;
const TOKEN_R_PX = 9, TOKEN_ACTIVE_R_PX = 12;
const OWNER_RAMP_S = 0.15;
const BALL_GLIDE_MPS = 15.0;
const LOFT_SCALE = 0.4;          // screen pixels of lift per pixel of pitch metre
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
  buildStoppages();
  buildBallModel();
  initHeader();
  buildRunningStats();
  wireControls();
  onEventChanged();
  requestAnimationFrame(render);
  setTimeout(fillPace, 50);
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
// <= cap; r=0 is constant speed (a mean above the cap is left to pacing).
function rampFor(meanSpeed, cap) { return Math.max(0, Math.min(0.25, 1 - meanSpeed / cap)); }
// Integral of smoothstep(u) from 0 to x (x in [0,1]): x^3 - x^4/2, which is 0
// at x=0, 0.5 at x=1 -- exactly half of a same-height rectangle, same as a
// linear ramp's triangle. That's what lets this reuse the old trapezoid's
// timing/peak-speed math unchanged (same r, same peak = 1/(1-r), same total
// distance in duration 1) while replacing its velocity SHAPE.
function smoothRampArea(x) { return x * x * x - x * x * x * x / 2; }

// Eased trapezoid: accelerate for fraction r of the duration, cruise at peak
// speed, decelerate for r. Unlike a plain (linear-velocity) ramp, this one's
// velocity is a smoothstep S-curve, so acceleration itself eases to zero at
// both ends of each ramp -- no sudden kink where cruise speed is reached or
// left. The closer a carry's mean speed sits to its cap, the shorter r gets
// (see rampFor), so this matters most on exactly the fastest, most visible
// carries -- a plain trapezoid there looked like "snaps up to speed, cruises,
// slams to a stop" (reported after watching a real build).
function trapezoid(f, r) {
  if (r <= 1e-6) return f;
  const peak = 1 / (1 - r);
  if (f < r) return peak * r * smoothRampArea(f / r);
  if (f > 1 - r) return 1 - peak * r * smoothRampArea((1 - f) / r);
  return peak * r * 0.5 + peak * (f - r);
}
function smoothstep(f) { f = Math.max(0, Math.min(1, f)); return f * f * (3 - 2 * f); }

// Time of this player's real anchor at exactly this (own-frame) location: the
// event time itself, unless build_match re-timed it later (D9, up to 5 s) or
// kept an identical duplicate up to MERGE_S earlier instead. null when that
// anchor was dropped.
function anchorTimeNear(tr, x, y, t) {
  const A = tr.anchors;
  for (let i = Math.max(0, bsearch(tr.anchorT, t - 0.06)); i < A.length && A[i][0] <= t + 5; i++) {
    if (A[i][0] >= t - 0.06 && Math.abs(A[i][1] - x) < 0.011 && Math.abs(A[i][2] - y) < 0.011) return A[i][0];
  }
  return null;
}

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
      // carries: [t0, t1, x0, y0, x1, y1, anomaly]. Drawn between the times of
      // the player's own start / carry_end anchors: those equal the event times
      // unless build_match re-timed a physically impossible one (D9; that is
      // also what "anomaly" carries are). A carry whose start or end anchor
      // was dropped is left to the reconstruction (ok=false), which does not
      // pass through that location.
      tr.carryObjs = (tr.carries || []).map(c => {
        const [et0, et1, x0, y0, x1, y1, anom] = c;
        const ts = anchorTimeNear(tr, x0, y0, et0), te = anchorTimeNear(tr, x1, y1, et1);
        const t0 = ts ?? et0, t1 = te ?? et1;
        const len = Math.hypot(x1 - x0, y1 - y0), dur = Math.max(t1 - t0, 1e-3);
        const mean = len / dur;
        const obj = { t0, t1, tEnd: t1, x0, y0, x1, y1, len, anom: !!anom, r: rampFor(mean, CARRY_CAP_MPS), ok: ts != null && te != null && t1 > t0 };
        // an inner real anchor off the straight path would be violated: keep the reconstruction there
        for (const a of tr.anchors) {
          if (obj.ok && a[0] > t0 + 0.02 && a[0] < t1 - 0.02) {
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
    if (c && c.ok && t >= c.t0 && t <= c.tEnd) return c;
  }
  return null;
}

// The reconstruction passes through the carry's start and end anchors, so the
// real carry path is position-continuous with it at both edges without any
// blending (CHANGELOG_fix.md, D6).
function carryPosition(tr, t) {
  const c = activeCarry(tr, t);
  return c ? carryPathAt(c, t) : null;
}

function anticipation(tr, t) {
  const i = bsearch(tr.recT0, t);
  for (let k = i; k >= 0 && k >= i - 2; k--) {
    const r = tr.recObjs[k];
    if (!r || !r.ok || t < r.t_pass || t > r.t_receive) continue;
    const p0 = baseAt(tr, r.t_pass, 'tactical'), p1 = baseAt(tr, r.t_receive, 'tactical');
    const dur = r.t_receive - r.t_pass, mean = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / dur;
    if (mean > MAX_SPEED_MPS) return null;
    const s = trapezoid((t - r.t_pass) / dur, rampFor(mean, RUN_CAP_MPS));
    return [p0[0] + (p1[0] - p0[0]) * s, p0[1] + (p1[1] - p0[1]) * s];
  }
  return null;
}

function playerPositionAt(tr, t) {
  const base = baseAt(tr, t, displayMode);
  const c = carryPosition(tr, t);
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

// The drawn ball. Knots are real on-ball event locations (HOME frame), each at
// its own event time (or its player's anchor time where build_match re-timed
// that anchor, D9). The path is a chain of segments, each starting exactly
// where the previous one ended, so the ball can never jump:
//   flight   Pass / Shot: to the end location over the real duration (lofted
//            passes also get a height, see apexFor)
//   attach   a Carry, and any gap between two on-ball events of the SAME player
//            (a receipt, then his pass, with or without a Carry recorded): the
//            ball is on that player's drawn path, eased from where it was to
//            the recorded location of his next event
//   glide    after a receipt / recovery / keeper collection when someone else
//            acts next: on the holder, then over the last moment to the next
//            event's location
//   loose    after a release (clearance, block, duel, miscontrol, ...): rolls at
//            constant speed to the next event's location over the whole gap
// Real-data conflicts are resolved without moving any location:
//   - Ball Receipt* with outcome Incomplete (the intended receiver; the ball
//     never got to him) and keeper events that do not touch the ball (Shot
//     Faced, Goal Conceded, Penalty Conceded) are not knots;
//   - a flight followed within 0.5 s by another on-ball event ends at that
//     event (StatsBomb often records a pass end and the receipt at one instant
//     a few metres apart: the ball arrives at the receiver instead of arriving
//     elsewhere and jumping); a flight cut short by the next event ends where
//     it was at that moment;
//   - a single touch recorded at the same instant as where the ball was, but
//     elsewhere, is reached after distance / BALL_CAP_MPS (at most
//     MAX_HOP_S), never earlier than recorded; faster hops are slowed on screen
//     by pacing.
const BALL_TYPES = new Set(['Pass', 'Ball Receipt*', 'Carry', 'Shot', 'Clearance', 'Ball Recovery', 'Interception', 'Dribble',
  'Miscontrol', 'Goal Keeper', 'Block', 'Duel', 'Dispossessed', '50/50', 'Foul Won', 'Referee Ball-Drop', 'Shield']);
const HOLDING_TYPES = new Set(['Ball Receipt*', 'Carry', 'Ball Recovery', 'Shield', 'Dribble', 'Interception']);
const GK_HOLDING = new Set(['Collected', 'Smother']);
const GK_NOT_ON_BALL = new Set(['Shot Faced', 'Goal Conceded', 'Penalty Conceded']);
const FOLD_S = 0.5, MAX_HOP_S = 0.5;
// A flight / hop that would need more than EXTREME_S x slow motion is a data
// error (e.g. a 20 m pass recorded as lasting 0.01 s): its time is lengthened
// to need exactly that, the later events waiting for it (logged).
const EXTREME_S = 3;

function apexFor(height, dur) {
  const phys = 9.81 * dur * dur / 8;  // projectile apex for a flight lasting dur
  if (height === 'High Pass') return Math.min(30, Math.max(1.8, phys));
  if (height === 'Low Pass') return Math.min(1.5, phys);
  return 0;
}

let BM = null;               // {G: segments, ta: segment start times, owners, ownersT, knots}
let ballStats = {};
function buildBallModel() {
  const byId = new Map();
  for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) byId.set(tr.id, tr);
  const K = [];
  for (const e of events) {
    if (!BALL_TYPES.has(e.type) || e.x == null) continue;
    if (e.type === 'Ball Receipt*' && e.receiptOutcome) continue;
    if (e.type === 'Goal Keeper' && GK_NOT_ON_BALL.has(e.gkType)) continue;
    const tr = byId.get(e.playerId) || null;
    const [x0, y0] = toHomeFrame(e.x, e.y, e.isHome);
    const d = { src: e, tr, type: e.type, t0: e.t, x0, y0, t1: e.t, x1: x0, y1: y0, flight: false, carry: false, H: 0,
      holding: HOLDING_TYPES.has(e.type) || (e.type === 'Goal Keeper' && GK_HOLDING.has(e.gkType)) };
    // Anchor-snapping (aligning this knot's time with a D9-retimed anchor) is
    // applied ONLY to Carry knots, whose drawn ball position must stay
    // consistent with tr.carryObjs (which anchor-snaps the same way). Every
    // other on-ball event uses its own raw recorded time, like
    // common/ballpath.py's authoritative ball proxy does. Reason: a Carry's
    // end and the following event's own start often coincide exactly (same
    // instant, same place) and get merged into ONE anchor by
    // build_match.resolve_collisions; anchorTimeNear's forward search (up to
    // 5 s, to reach a legitimately far-retimed anchor) would then also match
    // that SAME retimed anchor for the following event's raw (unretimed)
    // time, silently pushing it seconds into the future, past other real
    // events, corrupting this knot list's chronological order (found via
    // season QA: onscreen ball speed up to ~2100 m/s on 101 of 380 matches).
    if (e.type === 'Carry') {
      const ta = tr ? anchorTimeNear(tr, e.x, e.y, e.t) : null;
      if (ta != null) d.t0 = ta;
    }
    if (e.endX != null && e.dur > 0 && (e.type === 'Pass' || e.type === 'Shot' || e.type === 'Carry')) {
      [d.x1, d.y1] = toHomeFrame(e.endX, e.endY, e.isHome);
      d.t1 = e.t + e.dur;
      if (e.type === 'Carry') {
        d.carry = true;
        const te = tr ? anchorTimeNear(tr, e.endX, e.endY, e.t + e.dur) : null;
        if (te != null) d.t1 = te;
      } else {
        d.flight = true;
        d.H = apexFor(e.passHeight, e.dur);
      }
      if (d.t1 <= d.t0) { d.t1 = d.t0; d.flight = d.carry = false; d.x1 = x0; d.y1 = y0; }
    }
    K.push(d);
  }
  K.sort((a, b) => a.t0 - b.t0);
  const G = [];
  let folded = 0, hops = 0, maxHop = 0, clipped = 0, lengthened = 0;
  let pT = K.length ? K[0].t0 : 0, pX = K.length ? K[0].x0 : PITCH_LEN_M / 2, pY = K.length ? K[0].y0 : PITCH_WID_M / 2, pEv = null;
  for (let i = 0; i < K.length; i++) {
    const d = K[i], n = K[i + 1];
    const tNext = n ? n.t0 : Infinity;
    let t0 = Math.max(d.t0, pT);
    const dist = Math.hypot(d.x0 - pX, d.y0 - pY);
    const start = d.flight || d.carry;
    // Delay reaching this knot's own recorded location if the gap since the
    // last one is too short for the distance at BALL_CAP_MPS (a real StatsBomb
    // timestamp can land two touches a couple of metres apart a few ms apart) —
    // regardless of whether this knot itself goes on to start a flight/carry:
    // the gap segment leading INTO a flight needs the same protection as one
    // leading into a point touch, or an instant hand-off into a shot/pass can
    // still spike (found via season QA on 3754348: 0.003 s for 2.7 m = 890 m/s
    // leading straight into a Shot's own start).
    if (dist > 0.01 && t0 - pT < dist / BALL_CAP_MPS) {
      const want = pT + Math.min(dist / BALL_CAP_MPS, Math.max(MAX_HOP_S, dist / (BALL_CAP_MPS * EXTREME_S)));
      const t0n = Math.min(want, Math.max(t0, tNext - 1e-3));
      if (t0n > t0) { hops++; maxHop = Math.max(maxHop, t0n - d.t0); t0 = t0n; }
    }
    // gap segment: from where the ball is to this event's location
    if (t0 > pT + 1e-6) {
      const kind = pEv && pEv.tr && d.tr === pEv.tr ? 'attach' : pEv && pEv.tr && pEv.holding ? 'glide' : 'loose';
      const g = { kind, ta: pT, tb: t0, A: [pX, pY], B: [d.x0, d.y0], tr: kind === 'loose' ? null : pEv.tr };
      if (kind === 'glide') g.tg = t0 - Math.min(t0 - pT, Math.max(0.3, Math.hypot(d.x0 - pX, d.y0 - pY) / BALL_GLIDE_MPS));
      G.push(g);
      pX = d.x0; pY = d.y0;
    }
    pT = t0;
    if (start && d.t1 > t0 + 1e-6) {
      let t1 = d.t1, x1 = d.x1, y1 = d.y1;
      if (d.flight && n && n.t0 - t1 > -0.05 && n.t0 - t1 < FOLD_S && n.t0 > t0 + 0.05) {
        t1 = n.t0; x1 = n.x0; y1 = n.y0; folded++;
      }
      if (t1 > tNext && tNext > t0 + 1e-3) {   // the next event happens before this one ends
        const f = (tNext - t0) / (t1 - t0);
        x1 = pX + (x1 - pX) * f; y1 = pY + (y1 - pY) * f; t1 = tNext; clipped++;
      }
      const minDur = Math.hypot(x1 - pX, y1 - pY) / (BALL_CAP_MPS * EXTREME_S);
      if (d.flight && t1 - t0 < minDur) { t1 = t0 + minDur; lengthened++; }
      if (t1 > t0 + 1e-6) {
        G.push(d.flight ? { kind: 'flight', ta: t0, tb: t1, A: [pX, pY], B: [x1, y1], H: d.H, src: d.src }
          : { kind: 'attach', ta: t0, tb: t1, A: [pX, pY], B: [x1, y1], tr: d.tr, carry: true });
        pT = t1; pX = x1; pY = y1;
      }
    }
    pEv = d;
  }
  const owners = [];
  for (const g of G) {
    if (g.kind === 'attach' && g.tr) owners.push([g.ta, g.tb, g.tr]);
    else if (g.kind === 'glide') owners.push([g.ta, g.tg, g.tr]);
  }
  const merged = [];
  for (const o of owners) {
    const m = merged[merged.length - 1];
    if (m && m[2] === o[2] && o[0] - m[1] < 0.05) m[1] = Math.max(m[1], o[1]);
    else if (o[1] > o[0]) merged.push([...o]);
  }
  for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) tr.own = [];
  for (const o of merged) o[2].own.push([o[0], o[1]]);
  for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) tr.ownT = tr.own.map(o => o[0]);
  BM = { G, ta: G.map(g => g.ta), owners: merged, ownersT: merged.map(o => o[0]), first: K.length ? [K[0].x0, K[0].y0] : [pX, pY] };
  ballStats = { knots: K.length, segments: G.length, flights_ended_at_next_event: folded, flights_cut_short: clipped,
    touches_reached_late: hops, max_late_s: +maxHop.toFixed(3), extreme_flights_lengthened: lengthened };
}

// 0..1: how much this player is the one on the ball at t (0.15 s ramps).
function ownerAlpha(tr, t) {
  if (!tr.own || !tr.own.length) return 0;
  const i = bsearch(tr.ownT, t + OWNER_RAMP_S);
  let a = 0;
  for (let k = i; k >= 0 && k >= i - 1; k--) {
    const [s, e] = tr.own[k];
    a = Math.max(a, Math.min(1, (t - s) / OWNER_RAMP_S + 1, (e - t) / OWNER_RAMP_S + 1));
  }
  return Math.max(0, a);
}

function ownerAt(t) {
  const i = bsearch(BM.ownersT, t);
  const o = BM.owners[i];
  return o && t <= o[1] ? o[2] : null;
}

function playerHomeAt(tr, t) { const p = playerPositionAt(tr, t); return toHomeFrame(p[0], p[1], tr.isHome); }

const attachCache = { tactical: new Map(), realism: new Map() };
// On tr's drawn path between ta and tb, eased from A (at ta) to B (at tb),
// plus a small lead in the direction he is moving, faded out at both ends.
function attachedBall(g, t, tb) {
  const cache = attachCache[displayMode];
  let c = cache.get(g);
  if (!c) {
    const p0 = playerHomeAt(g.tr, g.ta), p1 = playerHomeAt(g.tr, tb);
    c = [g.A[0] - p0[0], g.A[1] - p0[1], g.kind === 'glide' ? 0 : g.B[0] - p1[0], g.kind === 'glide' ? 0 : g.B[1] - p1[1]];
    cache.set(g, c);
  }
  const p = playerHomeAt(g.tr, t), q = playerHomeAt(g.tr, t - 0.2), r = playerHomeAt(g.tr, t + 0.2);
  const w = tb > g.ta ? smoothstep((t - g.ta) / (tb - g.ta)) : (t > g.ta ? 1 : 0);
  let x = p[0] + c[0] * (1 - w) + c[2] * w, y = p[1] + c[1] * (1 - w) + c[3] * w;
  const vx = (r[0] - q[0]) / 0.4, vy = (r[1] - q[1]) / 0.4, v = Math.hypot(vx, vy);
  if (v > 0.05) {
    const off = BALL_OFFSET_M * Math.min(1, v / 1.5) * Math.max(0, Math.min(1, (t - g.ta) / 0.3, (tb - t) / 0.3));
    x += vx / v * off; y += vy / v * off;
  }
  return [x, y];
}

// Ball state from the event model alone (no stoppage treatment).
function ballModelAt(t) {
  const G = BM.G;
  const k = bsearch(BM.ta, t);
  if (k < 0) return { x: BM.first[0], y: BM.first[1], h: 0, flight: null };
  const g = G[k];
  if (t >= g.tb) return { x: g.B[0], y: g.B[1], h: 0, flight: null };
  const f = (t - g.ta) / (g.tb - g.ta);
  if (g.kind === 'flight')
    return { x: g.A[0] + (g.B[0] - g.A[0]) * f, y: g.A[1] + (g.B[1] - g.A[1]) * f, h: 4 * g.H * f * (1 - f), flight: g, f };
  if (g.kind === 'attach') { const [x, y] = attachedBall(g, t, g.tb); return { x, y, h: 0, flight: null }; }
  if (g.kind === 'glide') {
    if (t <= g.tg && g.tg > g.ta + 1e-6) { const [x, y] = attachedBall(g, t, g.tg); return { x, y, h: 0, flight: null }; }
    const P = g.tg <= g.ta + 1e-6 ? g.A : g.hold && g.hold[displayMode] || ((g.hold = g.hold || {})[displayMode] = attachedBall(g, g.tg, g.tg));
    const s = smoothstep((t - g.tg) / (g.tb - g.tg));
    return { x: P[0] + (g.B[0] - P[0]) * s, y: P[1] + (g.B[1] - P[1]) * s, h: 0, flight: null };
  }
  return { x: g.A[0] + (g.B[0] - g.A[0]) * f, y: g.A[1] + (g.B[1] - g.A[1]) * f, h: 0, flight: null };
}

// Ball in the HOME frame, including the stoppage sequence: from the moment the
// ball goes out / the whistle, it stays where it went dead through the hold,
// is carried to the restart spot while the dead time is skipped, and waits
// there for the restart.
function ballStateAt(t) {
  const s = stoppageAt(t);
  if (s && s.x != null) {
    const A = s.outBall[displayMode] || (s.outBall[displayMode] = ballModelAt(s.outT));
    const a = s.hasSkip ? s.skipA : s.outT + (s.end - s.outT) * 0.3;
    const b = s.hasSkip ? s.skipB : s.end - (s.end - s.outT) * 0.3;
    const w = smoothstep((t - a) / Math.max(b - a, 1e-6));
    return { x: A.x + (s.x - A.x) * w, y: A.y + (s.y - A.y) * w, h: 0, flight: null, dead: true };
  }
  return ballModelAt(t);
}

function ballHomePositionAt(t) { const b = ballStateAt(t); return [b.x, b.y]; }

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

// Every drawn player at t, HOME frame: {tr, x, y, sd, own (0..1 on the ball),
// sx, sy (display-only shift from real spacing, which the ball follows)}.
function framePositions(t, withSpacing = true) {
  const out = [];
  for (const side of ['home', 'away']) {
    const list = [];
    for (const tr of MATCH_DATA[side].tracks) if (trackActiveAt(tr, t)) list.push([tr, playerPositionAt(tr, t)]);
    let cx = 0, cy = 0, n = 0;
    if (withSpacing && realSpacing) for (const [tr, p] of list) if (tr.role !== 'GK') { cx += p[0]; cy += p[1]; n++; }
    for (const [tr, p] of list) {
      let x = p[0], y = p[1];
      if (n && tr.role !== 'GK') {
        const w = Math.min(1, gapToAnchor(tr, t) / SPACING_FADE_S), k = 1 + (SPACING_K - 1) * w;
        x = cx / n + (x - cx / n) * k; y = cy / n + (y - cy / n) * k;
      }
      const [hx, hy] = toHomeFrame(x, y, tr.isHome), [rx, ry] = toHomeFrame(p[0], p[1], tr.isHome);
      out.push({ tr, x: hx, y: hy, sd: p[2], own: ownerAlpha(tr, t), sx: hx - rx, sy: hy - ry });
    }
  }
  return out;
}

const TEAM_STYLE = { home: { main: '#7B003A', accent: '#94BEE5' }, away: { main: '#E30613', accent: '#FFFFFF' } };

function drawFormations(t, currEv, period, frame) {
  const s = stoppageBannerAt(t);
  const restartId = s && t >= (s.hasSkip ? s.skipB : s.outT) && t <= s.end + 0.8 ? s.playerId : null;
  const order = frame.slice().sort((a, b) => a.own - b.own);   // the player on the ball is drawn on top
  for (const f of order) {
    const pos = transformCoords(f.x, f.y, true, period);
    const st = f.tr.isHome ? TEAM_STYLE.home : TEAM_STYLE.away;
    drawPlayerToken(pos.px, pos.py, f.tr.name, st.main, st.accent, currEv.playerId === f.tr.id,
      displayMode === 'realism' ? f.sd : null, f.own, restartId === f.tr.id ? st.accent : null);
  }
}

function drawPlayerToken(x, y, name, mainColor, accentColor, isActive, sd, own = 0, restartColor = null) {
  const r = isActive ? TOKEN_ACTIVE_R_PX : TOKEN_R_PX;
  const calib = MATCH_DATA.calibration;
  if (sd !== null && sd !== undefined && calib && showUncertainty) {
    const ringR = Math.min(metersToPixels(sd * calib.k68), 55);
    ctx.beginPath(); ctx.arc(x, y, ringR, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(148,163,184,${Math.max(0.08, 0.38 - ringR / 160)})`; ctx.lineWidth = 1; ctx.stroke();
  }
  if (own > 0.01) {   // on the ball: soft white halo
    const g = ctx.createRadialGradient(x, y, r, x, y, r + 9);
    g.addColorStop(0, `rgba(255,255,255,${0.55 * own})`); g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r + 9, 0, Math.PI * 2); ctx.fill();
  }
  if (restartColor) {
    ctx.strokeStyle = restartColor; ctx.lineWidth = 2; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.arc(x, y, r + 7, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
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

// Ball drawn at its ground position; a lofted pass is drawn raised above its
// ground shadow by its height (apexFor: from the pass's own real duration),
// with a dashed ground trail. Ground position, and so the flight's timing and
// every recorded location, are the same for all heights.
function drawBallAndTrail(t, period, frame) {
  const b = ballStateAt(t);
  let x = b.x, y = b.y;
  const own = ownerAt(t);
  if (own) { const f = frame.find(q => q.tr === own); if (f) { x += f.sx; y += f.sy; } }
  const g = transformCoords(x, y, true, period);
  const d = b.flight;
  if (showTrails && d && d.src.type !== 'Carry') {
    const ev = d.src, s = transformCoords(d.A[0], d.A[1], true, period);
    ctx.beginPath(); ctx.moveTo(s.px, s.py); ctx.lineTo(g.px, g.py);
    ctx.strokeStyle = ev.isGoal ? '#FACC15' : ev.isShot ? '#FB923C' : ev.passOutcome === 'Complete' ? (ev.isHome ? 'rgba(148,190,229,0.85)' : 'rgba(255,255,255,0.85)') : 'rgba(239,68,68,0.7)';
    ctx.lineWidth = ev.isShot ? 3 : 2.5;
    if (d.H > 1.6) ctx.setLineDash([7, 5]);
    ctx.stroke(); ctx.setLineDash([]);
  }
  const lift = metersToPixels(b.h) * LOFT_SCALE, grow = 1 + Math.min(b.h, 20) / 30;
  const sh = Math.max(0.55, 1 / (1 + b.h / 10));   // the shadow marks the true ground position
  ctx.fillStyle = `rgba(0,0,0,${b.h > 0.3 ? 0.55 : 0.4})`;
  ctx.beginPath(); ctx.ellipse(g.px + (b.h > 0.3 ? 0 : 1), g.py + (b.h > 0.3 ? 0 : 3), 5.5 * sh, 3.3 * sh, 0, 0, Math.PI * 2); ctx.fill();
  if (lift > 2) {
    ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(g.px, g.py); ctx.lineTo(g.px, g.py - lift); ctx.stroke();
  }
  ctx.fillStyle = '#FFFFFF'; ctx.beginPath(); ctx.arc(g.px, g.py - lift, 5.5 * grow, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#0F172A'; ctx.lineWidth = 1.2; ctx.stroke();
}

// ---- stoppages ----------------------------------------------------------------
// Each stoppage (pipeline/build_match.detect_stoppages) is shown as:
//   outT .. outT+HOLD_OUT_S      real play: the ball arriving out / the foul,
//                                 an incident pulse, the banner fades in
//   skipA .. skipB               the dead time, skipped in SKIP_PLAYBACK_S of
//                                 playback, pitch dimmed with a "skipping" chip;
//                                 the ball is taken to the restart spot
//   skipB .. end                 real play: the restart set-up (LEAD_IN_S)
//   end .. end+BANNER_AFTER_S    the restart itself, then the banner fades out
// Short stoppages (dead time < SKIP_MIN_S) keep the banner but are not skipped.
let STOPS = [], STOPS_OUT = [], SKIPS = [], SKIPS_A = [];
function buildStoppages() {
  STOPS = (MATCH_DATA.stoppages || []).map(s => {
    const outT = s.outT != null ? s.outT : s.start;
    const skipA = outT + HOLD_OUT_S, skipB = s.end - LEAD_IN_S;
    const hasSkip = skipB - skipA >= SKIP_MIN_S;
    const o = { ...s, outT, skipA, skipB, hasSkip, outBall: {} };
    if (hasSkip) { o.D = skipB - skipA; o.P = SKIP_PLAYBACK_S; o.lam = Math.min(1, o.P / o.D); }
    return o;
  }).sort((a, b) => a.outT - b.outT);
  STOPS_OUT = STOPS.map(s => s.outT);
  SKIPS = STOPS.filter(s => s.hasSkip);
  SKIPS_A = SKIPS.map(s => s.skipA);
}

function stoppageAt(t) {   // the stoppage whose dead ball covers t
  const s = STOPS[bsearch(STOPS_OUT, t)];
  return s && t <= s.end ? s : null;
}
function stoppageBannerAt(t) {
  const s = STOPS[bsearch(STOPS_OUT, t)];
  return s && t <= s.end + BANNER_AFTER_S + BANNER_FADE_S ? s : null;
}
function skipAt(t) {
  const s = SKIPS[bsearch(SKIPS_A, t)];
  return s && t < s.skipB ? s : null;
}
function inSkip(t) { return !!skipAt(t); }

// Inside a skip, playback fraction u in [0,1] -> match time. Rate at both ends
// equals normal speed (lam), smoothly faster in between.
function skipMatchAt(s, u) { return s.skipA + s.D * (s.lam * u + (1 - s.lam) * smoothstep(u)); }
function skipUAt(s, t) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 40; i++) { const m = (lo + hi) / 2; if (skipMatchAt(s, m) < t) lo = m; else hi = m; }
  return (lo + hi) / 2;
}

function restartText(s) {
  const team = s.isHome ? MATCH_DATA.home.name : MATCH_DATA.away.name;
  const other = s.isHome ? MATCH_DATA.away.name : MATCH_DATA.home.name;
  switch (s.kind) {
    case 'throw_in': return `Throw-in — ${team}`;
    case 'corner': return `Corner — ${team}`;
    case 'goal_kick': return `Goal kick — ${team}`;
    case 'foul': return `Foul — ${team} free kick`;
    case 'offside': return `Offside — ${team} free kick`;
    case 'penalty': return `Penalty — ${team}`;
    case 'goal': return `Goal — ${other} · ${team} kick off`;
    default: return `Free kick — ${team}`;
  }
}

function drawStoppage(t, period) {
  const s = stoppageBannerAt(t);
  if (!s) return;
  const w = canvas.width, h = canvas.height;
  // incident pulse where the ball went dead
  if (t >= s.outT && t <= s.outT + 0.9) {
    const A = s.outBall[displayMode] || (s.outBall[displayMode] = ballModelAt(s.outT));
    const p = transformCoords(A.x, A.y, true, period), f = (t - s.outT) / 0.9;
    ctx.strokeStyle = s.kind === 'foul' || s.kind === 'offside' || s.kind === 'penalty' ? `rgba(250,204,21,${1 - f})`
      : s.kind === 'goal' ? `rgba(250,204,21,${1 - f})` : `rgba(255,255,255,${1 - f})`;
    ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(p.px, p.py, 8 + 22 * f, 0, Math.PI * 2); ctx.stroke();
  }
  // skipped dead time
  if (s.hasSkip && t >= s.skipA && t < s.skipB) {
    const u = skipUAt(s, t), env = Math.min(1, u / 0.15, (1 - u) / 0.15);
    ctx.fillStyle = `rgba(6,10,20,${0.45 * env})`; ctx.fillRect(0, 0, w, h);
    const secs = Math.round(s.D), txt = `⏩  skipping ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')} of stoppage`;
    ctx.font = '700 14px system-ui, sans-serif';
    const bw = ctx.measureText(txt).width + 36, bh = 40, bx = (w - bw) / 2, by = h / 2 - bh / 2;
    ctx.globalAlpha = env;
    ctx.fillStyle = 'rgba(15,23,42,0.92)'; ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 10); ctx.fill();
    ctx.fillStyle = '#E2E8F0'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(txt, w / 2, by + 16);
    ctx.fillStyle = 'rgba(148,163,184,0.35)'; ctx.fillRect(bx + 14, by + bh - 9, bw - 28, 3);
    ctx.fillStyle = '#FACC15'; ctx.fillRect(bx + 14, by + bh - 9, (bw - 28) * u, 3);
    ctx.globalAlpha = 1;
  }
  // restart banner, team-coloured, eased in and out
  const fin = (t - s.outT) / BANNER_FADE_S, fout = (s.end + BANNER_AFTER_S + BANNER_FADE_S - t) / BANNER_FADE_S;
  const a = smoothstep(Math.min(fin, fout));
  if (a <= 0) return;
  const st = s.isHome ? TEAM_STYLE.home : TEAM_STYLE.away;
  const txt = restartText(s);
  ctx.font = '700 16px system-ui, sans-serif';
  const bw = ctx.measureText(txt).width + 44, bh = 36, bx = (w - bw) / 2, by = 12 - 10 * (1 - a);
  ctx.globalAlpha = a;
  ctx.fillStyle = 'rgba(15,23,42,0.92)'; ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 9); ctx.fill();
  ctx.fillStyle = st.main; ctx.beginPath(); ctx.roundRect(bx, by, 12, bh, [9, 0, 0, 9]); ctx.fill();
  ctx.strokeStyle = st.accent; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 9); ctx.stroke();
  ctx.fillStyle = '#F8FAFC'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(txt, w / 2 + 6, by + bh / 2 + 1);
  ctx.globalAlpha = 1;
}

// ---- pacing -------------------------------------------------------------------
// S(t) = playback seconds per match second, on PACE_BIN_S bins, per display
// mode, computed lazily in PACE_CHUNK_S chunks from what is actually drawn:
//   stretch  max over players / ball of on-screen speed / its cap (CARRY_CAP
//            for the player on the ball, RUN_CAP otherwise, BALL_CAP). Max-
//            filtered over +-STRETCH_DILATE_S, then Gaussian-blurred with a
//            support inside that window, so the blurred value is still >= the
//            raw need everywhere (the cap holds) and slow-downs ease in.
//   slack    max of speed / comfort, clamped to [1/COMPRESS_MAX, 1], max-
//            filtered over +-SLACK_DILATE_S (only sustained idle play) and
//            blurred.
//   S = max(stretch, slack), at most STRETCH_MAX. Stoppage skips are handled
//   separately (skipMatchAt). The user's playback speed multiplies on top.
const pace = {};
function paceProfile(mode) {
  if (!pace[mode]) {
    const n = Math.ceil(maxT / PACE_BIN_S) + 2;
    pace[mode] = { S: new Float32Array(n).fill(1), done: new Uint8Array(Math.ceil(maxT / PACE_CHUNK_S) + 1), maxRaw: 0, capped: 0 };
  }
  return pace[mode];
}
function resetPacing() { for (const k of Object.keys(pace)) delete pace[k]; }

function dilateBlur(raw, R, sigma) {
  const n = raw.length, dil = new Float32Array(n), out = new Float32Array(n);
  for (let i = 0; i < n; i++) { let m = -Infinity; for (let k = Math.max(0, i - R); k <= Math.min(n - 1, i + R); k++) if (raw[k] > m) m = raw[k]; dil[i] = m; }
  const W = Math.min(R, Math.ceil(3 * sigma)), ker = [];
  let sum = 0;
  for (let k = -W; k <= W; k++) { const v = Math.exp(-0.5 * (k / sigma) ** 2); ker.push(v); sum += v; }
  for (let i = 0; i < n; i++) {
    let acc = 0, ws = 0;
    for (let k = -W; k <= W; k++) { const j = i + k; if (j < 0 || j >= n) continue; acc += dil[j] * ker[k + W]; ws += ker[k + W]; }
    out[i] = acc / ws;
  }
  return out;
}

// Peak speed inside one bin [t, t + PACE_BIN_S] from 4 sub-steps (a curve can
// peak ~15 % above its bin average); only called for things already fast.
function subBinPeak(posAt, t) {
  const n = 4, h = PACE_BIN_S / n;
  let prev = posAt(t), peak = 0;
  for (let i = 1; i <= n; i++) {
    const p = posAt(t + i * h);
    peak = Math.max(peak, Math.hypot(p[0] - prev[0], p[1] - prev[1]) / h);
    prev = p;
  }
  return peak;
}

function computePaceChunk(mode, c) {
  const P = paceProfile(mode);
  const margin = STRETCH_DILATE_S + SLACK_DILATE_S;
  const b0 = Math.max(0, Math.round((c * PACE_CHUNK_S - margin) / PACE_BIN_S));
  const b1 = Math.min(P.S.length, Math.round(((c + 1) * PACE_CHUNK_S + margin) / PACE_BIN_S));
  const n = b1 - b0;
  if (n <= 0) { P.done[c] = 1; return; }
  const prevMode = displayMode; displayMode = mode;
  const need = new Float32Array(n), slack = new Float32Array(n).fill(1 / COMPRESS_MAX);
  const cuts = (MATCH_DATA.periods || []).slice(1).map(p => p.start);
  let prev = null;
  for (let k = 0; k <= n; k++) {
    const t = (b0 + k) * PACE_BIN_S;
    if (t > maxT || inSkip(t)) { prev = null; if (k < n) slack[k] = 1; continue; }
    const fr = framePositions(t, false), ball = ballStateAt(t);
    const cur = { t, map: new Map(fr.map(f => [f.tr, f])), ball };
    if (prev && !cuts.some(x => prev.t < x && t >= x)) {
      const i = k - 1;
      let nd = 0, sl = 0;
      for (const f of fr) {
        const q = prev.map.get(f.tr);
        if (!q) continue;
        const cap = Math.max(f.own, q.own) > 0.5 ? CARRY_CAP_MPS : RUN_CAP_MPS;
        let v = Math.hypot(f.x - q.x, f.y - q.y) / PACE_BIN_S;
        sl = Math.max(sl, v / PLAYER_COMFORT_MPS);
        if (v > 0.6 * cap) v = Math.max(v, subBinPeak(tt => playerHomeAt(f.tr, tt), prev.t));
        nd = Math.max(nd, v / cap);
      }
      let vb = Math.hypot(ball.x - prev.ball.x, ball.y - prev.ball.y) / PACE_BIN_S;
      sl = Math.max(sl, vb / BALL_COMFORT_MPS);
      if (vb > 0.6 * BALL_CAP_MPS) vb = Math.max(vb, subBinPeak(tt => { const s = ballStateAt(tt); return [s.x, s.y]; }, prev.t));
      nd = Math.max(nd, vb / BALL_CAP_MPS);
      need[i] = nd; slack[i] = Math.min(1, Math.max(1 / COMPRESS_MAX, sl));
    } else if (k > 0) slack[k - 1] = 1;
    prev = cur;
  }
  // sub-bin events: real carries and ball flights shorter than a bin
  const lo = b0 * PACE_BIN_S, hi = b1 * PACE_BIN_S;
  const mark = (ta, tb, v) => {
    for (let b = Math.floor(ta / PACE_BIN_S); b <= Math.floor(tb / PACE_BIN_S); b++) if (b >= b0 && b < b1) need[b - b0] = Math.max(need[b - b0], v);
  };
  for (const side of ['home', 'away']) for (const tr of MATCH_DATA[side].tracks) for (const c2 of tr.carryObjs) {
    if (!c2.ok || c2.tEnd < lo || c2.t0 > hi || c2.tEnd - c2.t0 >= 2 * PACE_BIN_S) continue;
    mark(c2.t0, c2.tEnd, c2.len / Math.max(c2.tEnd - c2.t0, 1e-6) / (1 - c2.r) / CARRY_CAP_MPS);
  }
  for (let k = Math.max(0, bsearch(BM.ta, lo)); k < BM.G.length && BM.G[k].ta <= hi; k++) {
    const g = BM.G[k], a = g.kind === 'glide' ? g.tg : g.ta, len = Math.hypot(g.B[0] - g.A[0], g.B[1] - g.A[1]);
    if (g.tb - a >= 2 * PACE_BIN_S) continue;
    const peak = g.kind === 'flight' || g.kind === 'loose' ? 1 : 1.5;   // smoothstep peaks at 1.5x its mean
    mark(a, g.tb, len * peak / Math.max(g.tb - a, 1e-6) / BALL_CAP_MPS);
  }
  const st = dilateBlur(need, Math.round(STRETCH_DILATE_S / PACE_BIN_S), STRETCH_SIGMA_S / PACE_BIN_S);
  const sk = dilateBlur(slack, Math.round(SLACK_DILATE_S / PACE_BIN_S), SLACK_SIGMA_S / PACE_BIN_S);
  const c0 = Math.round(c * PACE_CHUNK_S / PACE_BIN_S), c1 = Math.min(P.S.length, Math.round((c + 1) * PACE_CHUNK_S / PACE_BIN_S));
  for (let b = c0; b < c1; b++) {
    const i = b - b0;
    let S = Math.max(st[i], sk[i]);
    P.maxRaw = Math.max(P.maxRaw, need[i]);
    if (S > STRETCH_MAX) { S = STRETCH_MAX; P.capped++; }
    P.S[b] = S;
  }
  P.done[c] = 1;
  displayMode = prevMode;
}

function paceS(t) {
  const P = paceProfile(displayMode);
  const b = Math.max(0, Math.min(P.S.length - 1, Math.floor(t / PACE_BIN_S + 1e-9)));
  const c = Math.floor(b * PACE_BIN_S / PACE_CHUNK_S + 1e-9);
  if (!P.done[c]) computePaceChunk(displayMode, c);
  return P.S[b];
}

// Advance the match clock by `budget` seconds of 1x playback.
function paceAdvance(t, budget) {
  for (let guard = 0; budget > 1e-9 && t < maxT && guard < 100000; guard++) {
    const s = skipAt(t);
    if (s) {
      const u = (t <= s.skipA ? 0 : skipUAt(s, t)) + budget / s.P;
      if (u >= 1) { budget = (u - 1) * s.P; t = s.skipB; continue; }
      return skipMatchAt(s, u);
    }
    const bin = Math.floor(t / PACE_BIN_S + 1e-9);
    const S = paceS(t);
    let tEnd = (bin + 1) * PACE_BIN_S;
    const ns = SKIPS[bsearch(SKIPS_A, t) + 1];
    if (ns && ns.skipA < tEnd && ns.skipA > t) tEnd = ns.skipA;
    if (tEnd <= t) tEnd = t + 1e-7;
    const need = (tEnd - t) * S;
    if (budget < need) return t + budget / S;
    budget -= need; t = tEnd;
  }
  return Math.min(t, maxT);
}

// Playback seconds (1x) between two match times, for reports.
function playbackBetween(a, b) {
  let p = 0, t = a;
  while (t < b - 1e-9) {
    const s = skipAt(t);
    if (s) { const e = Math.min(b, s.skipB); p += (skipUAt(s, e) - (t <= s.skipA ? 0 : skipUAt(s, t))) * s.P; t = e; continue; }
    let e = Math.min(b, (Math.floor(t / PACE_BIN_S + 1e-9) + 1) * PACE_BIN_S);
    const ns = SKIPS[bsearch(SKIPS_A, t) + 1];
    if (ns && ns.skipA < e && ns.skipA > t) e = ns.skipA;
    if (e <= t) e = t + 1e-7;
    p += (e - t) * paceS(t); t = e;
  }
  return p;
}

// Background filler: one chunk (~20 ms) per tick, starting just ahead of the
// playhead, so playback almost never has to compute one on the spot.
function fillPace() {
  const P = paceProfile(displayMode), n = P.done.length;
  const c0 = Math.max(0, Math.floor(matchClock / PACE_CHUNK_S));
  let worked = false;
  for (let i = 0; i < n; i++) {
    const c = (c0 + i) % n;
    if (!P.done[c]) { computePaceChunk(displayMode, c); worked = true; break; }
  }
  setTimeout(fillPace, !worked ? 1000 : isPlaying ? 30 : 10);   // idle once this mode is complete
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
  const dt = Math.min(0.1, (timestamp - lastTimestamp) / 1000);   // a hidden tab must not jump the match
  lastTimestamp = timestamp;
  if (isPlaying) {
    matchClock = paceAdvance(matchClock, dt * playbackSpeed);
    if (matchClock >= maxT) { matchClock = maxT; pause(); }
  }
  drawFrame(matchClock);
  requestAnimationFrame(render);
}

function drawFrame(t) {
  const newIndex = currentEventAt(t);
  if (newIndex !== currentIndex) { currentIndex = newIndex; onEventChanged(); }
  const currEv = events[currentIndex] || events[0];
  const period = periodAt(t);
  const frame = framePositions(t);
  drawPitch(period);
  drawFormations(t, currEv, period, frame);
  drawBallAndTrail(t, period, frame);
  drawStoppage(t, period);
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
    displayMode = btn.dataset.mode;
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
