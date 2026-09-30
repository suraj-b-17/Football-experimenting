// Headless harness: evals the REAL viewer/app.js in Node with DOM/canvas/fetch
// stubs, then runs a driver script in the same scope so it can call the
// viewer's own position functions (playerPositionAt, transformCoords, ...).
// Usage: node tests/viewer_harness.js <matchId> <driver.js> [outJson]
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const matchId = process.argv[2] || '3754129';
const driverPath = process.argv[3];
const appPath = process.env.APP_JS || path.join(ROOT, 'viewer', 'app.js');
const dataDir = process.env.DATA_DIR || path.join(ROOT, 'output', 'data');

const elements = {};
function makeEl(id) {
  if (!elements[id]) {
    elements[id] = {
      _id: id, textContent: '', value: '', innerHTML: '', style: {}, max: 0, dataset: {},
      classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
      addEventListener() {},
      querySelector() { return makeEl(id + '.child'); },
      querySelectorAll() { return []; },
    };
  }
  return elements[id];
}
const fakeCtx = new Proxy({}, {
  get(target, prop) {
    if (prop === 'measureText') return () => ({ width: 40 });
    if (typeof target[prop] !== 'undefined') return target[prop];
    return () => {};
  },
  set(target, prop, val) { target[prop] = val; return true; },
});
const canvasStub = { width: 960, height: 620, getContext: () => fakeCtx };
global.document = {
  getElementById(id) { return id === 'pitchCanvas' ? canvasStub : makeEl(id); },
  title: '', querySelectorAll() { return []; },
};
global.window = { addEventListener() {} };
global.location = { search: '?match=' + matchId, hash: '' };
global.requestAnimationFrame = () => 1;
// New payloads are data/<id>.js loaded by an injected <script>; old baseline
// payloads are .json loaded by fetch(). Support both, like the browser would.
const jsPath = path.join(dataDir, matchId + '.js'), jsonPath = path.join(dataDir, matchId + '.json');
global.fetch = async () => ({ ok: true, json: async () => JSON.parse(fs.readFileSync(jsonPath, 'utf8')) });
global.document.head = { appendChild(el) {
  setTimeout(() => {
    const src = path.join(dataDir, path.basename(el.src));
    if (!fs.existsSync(src)) { el.onerror && el.onerror(); return; }
    require('vm').runInThisContext(fs.readFileSync(src, 'utf8'));
    el.onload && el.onload();
  }, 0);
} };
global.document.createElement = () => ({});
global.window = global;  // payload scripts assign window.MATCH_DATA_<id>
global.window.addEventListener = () => {};

const appSrc = fs.readFileSync(appPath, 'utf8').replace(/\nboot\(\);\s*$/, '\n');
const driver = fs.readFileSync(driverPath, 'utf8');
// exit explicitly: the viewer keeps a background timer (pacing filler) alive
eval(appSrc + '\n(async () => { await boot();\n' + driver + '\n})().then(() => process.stdout.write("", () => process.exit(0)), e => { console.error(e); process.exit(1); });');
