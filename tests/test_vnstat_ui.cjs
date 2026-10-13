const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('vnstat/app/static/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const theme = script.slice(0, script.indexOf('const formatBytes'));
function run(parent) {
  const values = new Map();
  const style = {setProperty: (k, v) => values.set(k, v), removeProperty: k => values.delete(k)};
  const observers = [];
  const context = {window: {parent}, document: {documentElement: {style}},
    CSS: {supports: (_, value) => /^#[0-9a-f]{6}$/i.test(value)},
    MutationObserver: class {constructor(callback) {observers.push(callback);} observe() {}}};
  vm.runInNewContext(theme, context);
  return {values, style, observers};
}
const colors = {'--primary-background-color': '#112233', '--primary-color': '#abcdef'};
const host = {hass: {themes: {darkMode: true}}};
const result = run({document: {querySelector: () => host, documentElement: {}},
  getComputedStyle: () => ({getPropertyValue: key => colors[key] || ''})});
assert.equal(result.values.get('--bg'), '#112233');
assert.equal(result.values.get('--accent'), '#abcdef');
assert.equal(result.style.colorScheme, 'dark');
colors['--primary-background-color'] = '#ffffff';
colors['--primary-color'] = 'invalid';
host.hass.themes.darkMode = false;
result.observers[0]();
assert.equal(result.values.get('--bg'), '#ffffff');
assert.equal(result.values.has('--accent'), false);
assert.equal(result.style.colorScheme, 'light');
assert.equal(run({document: {querySelector: () => null}}).values.size, 0);
assert.equal(run({get document() {throw new Error('cross-origin');}}).values.size, 0);
assert.doesNotMatch(html, /box-shadow|--glow|radial-gradient/);
console.log('vnStat theme updates and embed fallbacks passed');
