const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// A tiny DOM substitute checks live updates without opening a browser.
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {};
    this.style = {setProperty: (key, value) => { this.style[key] = value; }, removeProperty: key => { delete this.style[key]; }};
    this.classList = {toggle: (key, value) => { this.attributes[key] = value; }};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute(key, value) { this.attributes[key] = value; }
  querySelector(tag) { return this.children.find(child => child.tag === tag); }
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
    trigger: {type: "time", start: "18:00", end: "06:00"}, targets: ["light.porch"]}],
    status: {porch: {reason: "Active", targets: {"light.porch": "Command sent"}}}});
`, context);
const card = vm.runInContext('overviewCards.get("porch")', context);
document.activeElement = card.picker; card.picker.value = "300";
vm.runInContext(`applySnapshot({...snapshot, status: {porch: {reason: "Snoozed", snooze_seconds: 300, targets: {}}}})`, context);
assert.equal(vm.runInContext('overviewCards.get("porch")', context), card);
assert.equal(card.badge.textContent, "Snoozed");
assert.equal(card.remaining.textContent, "5m left");
assert.equal(card.picker.value, "300");
assert.equal(card.toggle.attributes.role, "switch");
assert.equal(card.editButton.attributes["aria-label"], "Edit Porch");
vm.runInContext(`applySnapshot({...snapshot, status: {porch: {reason: "Active", snooze_seconds: 0, targets: {"light.porch": "In sync"}}}})`, context);
assert.equal(card.statuses.children[0].textContent, "Porch: In sync");
assert.equal(card.remaining.hidden, true);
vm.runInContext("stream.onerror()", context);
assert.equal(nodes.get("app-title").attributes.disconnected, true);
vm.runInContext("applySnapshot(snapshot)", context);
assert.equal(nodes.get("app-title").attributes.disconnected, false);
console.log("UI update and HA theme checks passed");
