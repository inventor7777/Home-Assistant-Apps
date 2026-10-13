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

const buttons = [...html.matchAll(/<button id="show-([^"]+)"[^>]*>([^<]+)<\/button>/g)];
assert.deepEqual(buttons.map(([, mode, label]) => [mode, label]), [
  ['normal', 'vnStat'], ['daily', 'Daily'], ['monthly', 'Monthly'], ['json', 'JSON']
]);
const controls = buttons.map(([, mode]) => ({id: `show-${mode}`, classList: {
  toggle(_, active) {this.active = active;}
}, setAttribute(_, value) {this.pressed = value;}, addEventListener(_, handler) {this.click = handler;}}));
const output = {};
const requests = [];
const cliContext = vm.createContext({document: {
  querySelectorAll: () => controls, getElementById: () => output
}, window: {scrollY: 0, scrollTo() {}}, fetch: async url => {
  requests.push(url);
  return {ok: true, json: async () => ({status: 'ok', output: ` ${url} `})};
}});
vm.runInContext(script.slice(script.indexOf('const setActiveCliButton'), script.indexOf('const load = async')), cliContext);
vm.runInContext(script.slice(script.lastIndexOf('for (const button'), script.indexOf('      load();')), cliContext);
(async () => {
  for (const control of controls) {
    control.click({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
    const mode = control.id.slice('show-'.length);
    assert.equal(requests.at(-1), `./api/cli/${mode}`);
    assert.equal(output.textContent, `./api/cli/${mode}`);
    assert.deepEqual(controls.filter(c => c.classList.active), [control]);
    assert.equal(control.pressed, 'true');
  }
  console.log('vnStat CLI button order, requests, and selection passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
