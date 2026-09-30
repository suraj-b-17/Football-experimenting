// Screenshots of the pitch canvas at given match times, in a real browser from
// file://, on either the pre-polish or the current viewer.
// Usage: NODE_PATH=<puppeteer-core> node tests/browser/phase_shots.js <site dir> <match id> <shots.json> <out dir> [chrome|firefox]
//   shots.json: [{name, t, crop?: [x, y, w, h] in canvas px}]
const puppeteer = require('puppeteer-core');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

const [site, matchId, shotsFile, outDir, browserName = 'chrome'] = process.argv.slice(2);
const BROWSERS = {
  chrome: { executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true },
  firefox: { browser: 'firefox', executablePath: 'C:/Program Files/Mozilla Firefox/firefox.exe', headless: true },
};
(async () => {
  const shots = JSON.parse(fs.readFileSync(shotsFile, 'utf8'));
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await puppeteer.launch(BROWSERS[browserName]);
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.setViewport({ width: 1400, height: 1000 });
  await page.goto(pathToFileURL(path.join(site, 'viewer.html')).href + `?match=${matchId}#match=${matchId}`);
  await page.waitForFunction(() => typeof MATCH_DATA !== 'undefined' && MATCH_DATA && MATCH_DATA.home, { timeout: 60000 });
  const canvas = await page.$('#pitchCanvas');
  for (const s of shots) {
    await page.evaluate(t => {
      pause(); matchClock = t;
      if (typeof drawFrame === 'function') drawFrame(t);                  // current viewer: pure function of t
      else {                                                              // pre-polish viewer: label only while its warp is active
        currentIndex = currentEventAt(t); onEventChanged();
        activeStoppage = displayMode === 'tactical' ? findStoppageAt(t) : null;
      }
    }, s.t);
    await new Promise(r => setTimeout(r, 150));
    await page.evaluate(t => { if (typeof drawFrame === 'function') drawFrame(t); }, s.t);
    const box = await canvas.boundingBox();
    const scale = box.width / 960;
    const clip = s.crop ? { x: box.x + s.crop[0] * scale, y: box.y + s.crop[1] * scale, width: s.crop[2] * scale, height: s.crop[3] * scale } : box;
    await page.screenshot({ path: path.join(outDir, `${s.name}.png`), clip });
  }
  console.log(JSON.stringify({ shots: shots.length, errors }));
  await browser.close();
})();
