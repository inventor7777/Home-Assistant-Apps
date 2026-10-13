const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// A tiny DOM substitute checks live updates without opening a browser.
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.dataset = {};
    this.style = {setProperty: (key, value) => { this.style[key] = value; }, removeProperty: key => { delete this.style[key]; }};
    this.classList = {toggle: (key, value) => { this.attributes[key] = value; }};
  }
  append(...children) { children.forEach(child => { child.parent = this; }); this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
  focus() { document.activeElement = this; }
  showModal() { document.activeElement = document.getElementById("name"); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute(key, value) { this.attributes[key] = value; }
  querySelector(tag) { return this.children.find(child => child.tag === tag); }
  querySelectorAll(tags) { return this.children.flatMap(child => [...(tags.split(",").includes(child.tag) ? [child] : []), ...child.querySelectorAll(tags)]); }
  get firstChild() { return this.children[0]; }
}
const nodes = new Map();
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element("div")); return nodes.get(id); },
  createElement: tag => new Element(tag), documentElement: new Element("html"),
};
const parentRoot = new Element("html"), host = {hass: {themes: {darkMode: true}}};
let themeChanged, observed;
const palette = {"--primary-background-color": "#123456", "--primary-color": "#abcdef"};
const context = vm.createContext({
  document, window: {parent: {document: {documentElement: parentRoot, querySelector: () => host}, getComputedStyle: () => ({getPropertyValue: key => palette[key] || ""})}},
  CSS: {supports: () => true}, MutationObserver: class {constructor(callback) { themeChanged = callback; } observe(element) { observed = element; }},
  EventSource: class {constructor(url) { this.url = url; }},
  crypto: require("node:crypto").webcrypto,
});
vm.runInContext(fs.readFileSync("state_sync/app/static/app.js", "utf8"), context);
assert.equal(observed, parentRoot);
assert.equal(document.documentElement.style["--bg"], "#123456");
assert.equal(document.documentElement.style.colorScheme, "dark");
palette["--primary-background-color"] = "#654321"; themeChanged();
assert.equal(document.documentElement.style["--bg"], "#654321");

vm.runInContext(`
  entities = [{entity_id: "light.porch", attributes: {friendly_name: "Porch"}}];
  applySnapshot({connected: true, rules: [{id: "porch", name: "Porch", enabled: true,
    trigger: {type: "time", start: "18:00", end: "06:00"}, desired: [{entity_id: "light.porch", state: "on"}]}],
    status: {porch: {reason: "Active", targets: {"light.porch": "Command sent"}}}});
`, context);
const card = vm.runInContext('overviewCards.get("porch")', context);
assert.equal(card.picker.parent.className, "card-controls");
assert.equal(card.picker.parent.children[1].children[0], card.toggle);
assert.equal(card.status.parent.className, "card-foot");
assert.equal(card.status.parent.children.at(-1), card.editButton);
document.activeElement = card.picker; card.picker.value = "300";
vm.runInContext(`applySnapshot({...snapshot, status: {porch: {reason: "Snoozed", snooze_seconds: 300, targets: {}}}})`, context);
assert.equal(vm.runInContext('overviewCards.get("porch")', context), card);
assert.equal(card.status.textContent, "Snoozed");
assert.equal(card.status.className, "card-status idle");
assert.equal(card.remaining.textContent, "5m left");
assert.equal(card.picker.value, "300");
assert.equal(card.toggle.attributes.role, "switch");
assert.equal(card.editButton.attributes["aria-label"], "Edit Porch");
vm.runInContext(`applySnapshot({...snapshot, status: {porch: {reason: "Enforcing", snooze_seconds: 0, targets: {"light.porch": "In sync"}}}})`, context);
assert.equal(card.status.textContent, "In sync");
assert.equal(card.status.className, "card-status synced");
assert.equal(card.statuses.children[0].textContent, "Porch: In sync");
assert.equal(card.remaining.hidden, true);
vm.runInContext("stream.onerror()", context);
assert.equal(nodes.get("app-title").attributes.disconnected, true);
vm.runInContext("applySnapshot(snapshot)", context);
assert.equal(nodes.get("app-title").attributes.disconnected, false);
for (const [reason, target, tone] of [["Enforcing alternate", "In sync", "alternate"],
  ["Enforcing alternate", "Command sent — waiting for device", "error"],
  ["Enforcing", "Unknown/unavailable — skipped", "error"],
  ["Enforcing", "Overridden by other sync", "error"]]) {
  context.liveStatus = {reason, targets: {"light.porch": target}};
  vm.runInContext("applySnapshot({...snapshot, status: {porch: liveStatus}})", context);
  assert.equal(card.status.className, `card-status ${tone}`);
}

vm.runInContext(`
  populate({name: "Mixed", trigger: {type: "entity", entity_id: "binary_sensor.night", value: "on"},
    desired: [{entity_id: "light.porch", state: "on", data: {brightness_pct: 30}}, {entity_id: "switch.other", state: "off"}],
    alternate: [{entity_id: "climate.house", state: "heat", data: {temperature: 21}}]});
`, context);
let edited = JSON.parse(vm.runInContext("readForm().yaml", context));
assert.equal(edited[0].desired.length, 2);
assert.equal(edited[0].desired[0].data.brightness_pct, 30);
assert.equal(edited[0].desired[1].state, "off");
assert.equal(edited[0].alternate[0].entity_id, "climate.house");
assert.equal(edited[0].alternate[0].data.temperature, 21);
assert.equal(nodes.get("alternate-section").attributes.collapsed, false);
assert.equal("targets" in edited[0], false);
nodes.get("desired").children[0].children[0].children[1].listeners.click();
assert.equal(nodes.get("desired").children.length, 1);
assert.equal(nodes.get("alternate").children.length, 1);

vm.runInContext(`entities = [{entity_id: "binary_sensor.night", attributes: {friendly_name: "Night sensor"}},
  {entity_id: "light.night", attributes: {friendly_name: "Night light"}}]`, context);
const targetSearch = nodes.get("add-desired").children[0].children[2];
targetSearch.children[0].children[0].value = "night";
targetSearch.children[0].children[0].listeners.input();
assert.equal(targetSearch.children[2].children.length, 1);
assert.equal(targetSearch.children[2].children[0].textContent, "Night light");
targetSearch.children[2].children[0].listeners.click();
assert.equal(nodes.get("desired").children.at(-1).entity, "light.night");
assert.equal(nodes.get("alternate").children.length, 1);
const sourceSearch = nodes.get("source").firstChild.children[0].children[2];
sourceSearch.children[0].children[0].value = "night";
sourceSearch.children[0].children[0].listeners.input();
assert.equal(sourceSearch.children[2].children[0].textContent, "Night light");
vm.runInContext(`const conditionPicker = entityPicker(new Set(), () => {});`, context);
const conditionSearch = vm.runInContext("conditionPicker.children[2]", context);
conditionSearch.children[0].children[0].value = "night";
conditionSearch.children[0].children[0].listeners.input();
assert.equal(conditionSearch.children[2].children.length, 2);

// Moving name/enabled outside #fields must preserve draft round trips and validation.
context.fetch = async (path, options) => {
  assert.equal(path, "api/preview");
  const body = JSON.parse(options.body), rule = JSON.parse(body.yaml)[0];
  return {ok: true, json: async () => ({rule, yaml: JSON.stringify([rule])})};
};
vm.runInContext(`
  $("form").dataset.newId = "draft";
  populate({name: "", enabled: false, trigger: {type: "entity", entity_id: "", value: "on"}, desired: []});
  setMode(false);
`, context);
assert.equal(nodes.get("name").required, true);
assert.equal(nodes.get("enabled").disabled, false);
assert.equal(nodes.get("mode-toggle").textContent, "YAML");
assert.equal(nodes.get("alternate-section").attributes.collapsed, true);
nodes.get("inverted").checked = true;
nodes.get("inverted").listeners.change();
assert.equal(nodes.get("alternate-section").attributes.collapsed, false);
nodes.get("inverted").checked = false;
nodes.get("inverted").listeners.change();
(async () => {
  await nodes.get("mode-toggle").listeners.click();
  assert.equal(nodes.get("mode-toggle").textContent, "Form");
  assert.equal(nodes.get("name").required, false);
  assert.equal(nodes.get("enabled").disabled, true);
  assert.equal(JSON.parse(nodes.get("yaml").value)[0].enabled, false);
  const draft = JSON.parse(nodes.get("yaml").value);
  draft[0].name = "Porch Light Night"; draft[0].enabled = true;
  nodes.get("yaml").value = JSON.stringify(draft);
  await nodes.get("mode-toggle").listeners.click();
  assert.equal(nodes.get("name").value, "Porch Light Night");
  assert.equal(nodes.get("enabled").checked, true);
  assert.equal(nodes.get("name").required, true);
  assert.equal(nodes.get("name").disabled, false);
  assert.equal(nodes.get("mode-toggle").textContent, "YAML");
  context.fetch = async () => ({ok: false, json: async () => ({error: "Invalid YAML"})});
  await nodes.get("mode-toggle").listeners.click();
  assert.equal(nodes.get("mode-toggle").textContent, "YAML");
  assert.equal(nodes.get("mode-toggle").disabled, false);
  assert.equal(nodes.get("edit-error").textContent, "Invalid YAML");
  context.fetch = async () => ({ok: true, json: async () => []});
  await vm.runInContext("openEditor()", context);
  assert.equal(document.activeElement, nodes.get("close"));
  console.log("UI updates, HA theme, and editor mode checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
