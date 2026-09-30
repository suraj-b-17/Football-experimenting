// Polish item 3: how each stoppage is entered on screen. 'flight': the ball is
// seen travelling to where it went dead (a Pass/Shot ending at out_t);
// 'incident': a foul/offside/goal (the whistle point is a real event location);
// 'last touch': the ball is shown where it was last touched (StatsBomb records
// no out-of-play location for clearances, blocks, saves, ...).
displayMode = 'tactical';
const out = { matchId: MATCH_ID, byKind: {} };
for (const s of STOPS) {
  const k = out.byKind[s.kind] || (out.byKind[s.kind] = { n: 0, skipped: 0, flight: 0, incident: 0, last_touch: 0, dead_s: 0, playback_s: 0 });
  k.n++; if (s.hasSkip) k.skipped++;
  const g = BM.G[bsearch(BM.ta, s.outT - 1e-3)];
  if (['foul', 'offside', 'goal', 'penalty'].includes(s.kind)) k.incident++;
  else if (g && g.kind === 'flight' && Math.abs(g.tb - s.outT) < 0.05) k.flight++;
  else k.last_touch++;
  k.dead_s += s.end - s.outT; k.playback_s += playbackBetween(s.outT, s.end);
}
for (const k of Object.values(out.byKind)) { k.dead_s = +k.dead_s.toFixed(0); k.playback_s = +k.playback_s.toFixed(0); }
console.log(JSON.stringify(out));
