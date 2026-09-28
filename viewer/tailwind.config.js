// Build-time only: compiles the utility classes actually used into viewer/viewer.css,
// so the viewer needs no CDN at runtime.  See README "Rebuilding the stylesheet".
module.exports = {
  content: ['./viewer/viewer.html', './viewer/app.js', './pipeline/run_season.py'],
  theme: { extend: {} },
  plugins: [],
};
