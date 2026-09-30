// Real-browser test of output/ opened from file:// (double-click), no server.
// Usage: NODE_PATH=<dir with puppeteer-core> node tests/browser/file_test.js [chrome|firefox ...]
// Fails (exit 1) on any console error, page error, failed request or non-file:// request.
const puppeteer = require('puppeteer-core');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

const OUT = process.env.OUT_DIR ? path.resolve(process.env.OUT_DIR) : path.resolve(__dirname, '..', '..', 'output');
const SHOTS = path.join(OUT, 'qa_screens');
const MATCHES = { '3754129': 'Arsenal v Liverpool', '3754348': 'Norwich 4-5 Liverpool',
  '3753984': 'Liverpool 0-3 West Ham (red card)', '3754258': 'Aston Villa 0-0 Man City', '3754078': 'Chelsea 2-2 Swansea' };
const BROWSERS = {
  chrome: { executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--window-size=1400,1000'] },
  firefox: { browser: 'firefox', executablePath: 'C:/Program Files/Mozilla Firefox/firefox.exe', headless: true },
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function run(name) {
  const problems = [], report = [];
  const browser = await puppeteer.launch(BROWSERS[name]);
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 1000 });
  if (name === 'chrome') await page.setOfflineMode(true);
  page.on('console', m => { if (m.type() === 'error') problems.push(`console.error: ${m.text()}`); });
  page.on('pageerror', e => problems.push(`pageerror: ${e.message}`));
  page.on('requestfailed', r => problems.push(`requestfailed: ${r.url()} ${r.failure() && r.failure().errorText}`));
  page.on('request', r => { if (!r.url().startsWith('file:') && !r.url().startsWith('data:')) problems.push(`non-local request: ${r.url()}`); });
  fs.mkdirSync(path.join(SHOTS, name), { recursive: true });

  const indexUrl = pathToFileURL(path.join(OUT, 'index.html')).href;
  for (const [id, label] of Object.entries(MATCHES)) {
    await page.goto(indexUrl, { waitUntil: 'load' });
    const nLinks = await page.$$eval('a[href^="viewer.html"]', a => a.length);
    await Promise.all([page.waitForNavigation({ waitUntil: 'load' }), page.click(`a[href^="viewer.html?match=${id}"]`)]);
    await page.waitForFunction(() => typeof MATCH_DATA !== 'undefined' && MATCH_DATA && MATCH_DATA.home, { timeout: 60000 });
    const r = { browser: name, id, label, index_links: nLinks, url: page.url().split('/output/')[1] };
    r.loaded_match = await page.evaluate(() => MATCH_DATA.matchId);
    // pitch + players: sample canvas pixels
    const px = async () => page.evaluate(() => {
      const c = document.getElementById('pitchCanvas'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let green = 0, home = 0, away = 0, white = 0;
      for (let i = 0; i < d.length; i += 4) {
        const [R, G, B] = [d[i], d[i + 1], d[i + 2]];
        if (G > R + 10 && G > B && R < 40) green++;
        if (Math.abs(R - 0x7B) < 12 && G < 20 && Math.abs(B - 0x3A) < 14) home++;
        if (R > 200 && G < 30 && B < 40) away++;
        if (R > 240 && G > 240 && B > 240) white++;
      }
      return { green, home, away, white };
    });
    r.pixels_at_load = await px();
    // playback 10 s
    await page.evaluate(() => { jumpToIndex(0); });
    const c0 = await page.evaluate(() => matchClock);
    await page.click('#btn-play');
    await sleep(10000);
    const c1 = await page.evaluate(() => matchClock);
    await page.click('#btn-play');
    r.playback_clock_advance_s = +(c1 - c0).toFixed(2);
    // scrub the timeline
    r.scrub = await page.evaluate(() => {
      const s = document.getElementById('time-slider'); s.value = String(Math.floor(totalEvents / 2));
      s.dispatchEvent(new Event('input')); return { index: currentIndex, clock: +matchClock.toFixed(2), expected: +events[Math.floor(totalEvents / 2)].t.toFixed(2) };
    });
    // display modes
    const modes = [];
    for (const m of ['realism', 'tactical']) { await page.click(`.btn-mode[data-mode="${m}"]`); modes.push(await page.evaluate(() => displayMode)); }
    r.modes = modes;
    // screenshots at 01:33 and ~60:00 (tactical, the default)
    for (const [tag, t] of [['0133', 93.743], ['60min', 3600]]) {
      await page.evaluate(tt => { pause(); matchClock = tt; currentIndex = currentEventAt(tt); onEventChanged(); }, t);
      await sleep(400);
      r['pixels_' + tag] = await px();
      r['in_skip_' + tag] = await page.evaluate(tt => typeof inSkip === 'function' && inSkip(tt), t);
      await page.screenshot({ path: path.join(SHOTS, name, `${id}_${tag}.png`) });
    }
    // both teams drawn in exact team colours at every screenshot not inside a
    // stoppage skip (a skip dims the pitch on purpose, so exact colours change)
    const teamsOk = ['0133', '60min'].every(tag => r['in_skip_' + tag] || (r['pixels_' + tag].home > 50 && r['pixels_' + tag].away > 50)) &&
      ['0133', '60min'].some(tag => !r['in_skip_' + tag]);
    const ok = r.loaded_match === id && r.pixels_at_load.green > 100000 && teamsOk &&
      // pacing: 10 s of 1x playback is not exactly 10 s of match (slowed for
      // fast movement, sped up for idle play / stoppages), but must be of that order
      r.playback_clock_advance_s >= 5 && r.playback_clock_advance_s <= 25 &&
      Math.abs(r.scrub.clock - r.scrub.expected) < 0.01 && modes.join() === 'realism,tactical';
    r.ok = ok;
    if (!ok) problems.push(`checks failed for ${id}: ${JSON.stringify(r)}`);
    report.push(r);
  }
  await browser.close();
  return { report, problems };
}

(async () => {
  const names = process.argv.slice(2).length ? process.argv.slice(2) : ['chrome', 'firefox'];
  let failed = false;
  for (const n of names) {
    let res;
    try { res = await run(n); } catch (e) { console.log(`${n}: FAILED TO RUN — ${e.message}`); failed = true; continue; }
    for (const r of res.report) console.log(JSON.stringify(r));
    console.log(`${n}: ${res.report.filter(r => r.ok).length}/${res.report.length} matches passed, ${res.problems.length} problems`);
    for (const p of res.problems) console.log('  PROBLEM', p);
    if (res.problems.length) failed = true;
  }
  process.exit(failed ? 1 : 0);
})();
