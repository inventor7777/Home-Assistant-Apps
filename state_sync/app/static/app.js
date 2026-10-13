"use strict";
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
let snapshot = {rules: [], revision: "", status: {}}, entities = [], selected = new Set();
let editing = null, editRevision = "", rawMode = false, actionDomain = "light";
const actionReaders = {};

async function api(path, body) {
  const response = await fetch(`api/${path}`, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-State-Sync": "1"}, body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function error(id, message) { $(id).textContent = message || ""; $(id).hidden = !message; }
function label(text, input) { const n = el("label", text); n.append(input); return n; }
function input(type, value = "") { const n = el("input"); n.type = type; n.value = value; return n; }
function select(options, value) { const n = el("select"); for (const [v, text] of options) { const o = el("option", text); o.value = v; n.append(o); } n.value = value; return n; }
function check(text, checked, onChange) { const i = input("checkbox"); i.checked = checked; i.addEventListener("change", onChange); const n = label(text, i); n.className = "check"; return n; }
function button(text, fn, cls = "quiet") { const b = el("button", text, cls); b.type = "button"; b.addEventListener("click", fn); return b; }
const friendly = id => entities.find(e => e.entity_id === id)?.attributes?.friendly_name || id;

async function refresh() {
  snapshot = await api("snapshot");
  $("connection").textContent = snapshot.connected ? "● Connected to Home Assistant" : "○ Home Assistant disconnected";
  $("timezone").textContent = snapshot.connected ? `Time windows · ${snapshot.timezone}` : "";
  error("error", snapshot.error);
  if (!$("overview").contains(document.activeElement)) renderOverview();
}
function renderOverview() {
  const root = $("overview"); root.replaceChildren();
  if (!snapshot.rules.length) { root.append(el("div", "No syncs yet. Create one to keep your devices in the right state.", "empty")); return; }
  for (const type of ["entity", "time"]) {
    const rules = snapshot.rules.filter(r => r.trigger.type === type);
    if (!rules.length) continue;
    const heading = el("div", undefined, "section-head"); heading.append(el("h2", type === "entity" ? "Entity syncs" : "Time syncs"), el("small", `${rules.length} sync${rules.length === 1 ? "" : "s"}`)); root.append(heading);
    const cards = el("div", undefined, "cards"); root.append(cards);
    for (const r of rules) {
      const s = snapshot.status[r.id] || {reason: "Waiting for evaluation", targets: {}};
      const card = el("article", undefined, "card"), head = el("div", undefined, "card-head");
      head.append(button(r.name, () => openEditor(r), "card-title"));
      head.append(check("Enabled", r.enabled !== false, async event => {
        const i = event.target; i.disabled = true;
        try { await api("rule", {id: r.id, revision: snapshot.revision, yaml: JSON.stringify([{...r, enabled: i.checked}])}); await refresh(); }
        catch (e) { i.checked = !i.checked; error("error", e.message); } finally { i.disabled = false; }
      }));
      card.append(head);
      const when = type === "time" ? `${r.trigger.start} – ${r.trigger.end}` : `${friendly(r.trigger.entity_id)} ${r.trigger.operator || "eq"} ${JSON.stringify(r.trigger.value)}`;
      card.append(el("p", `${when} → ${r.targets.map(friendly).join(", ")}`, "description"));
      card.append(el("span", s.reason, "badge"));
      const statuses = el("ul", undefined, "target-status");
      for (const [entity, reason] of Object.entries(s.targets)) statuses.append(el("li", `${friendly(entity)}: ${reason}`));
      card.append(statuses);
      const foot = el("div", undefined, "card-foot"); foot.append(el("span", `Priority ${r.priority ?? 50}`));
      const picker = select([["", "Snooze…"], [300, "5 minutes"], [1800, "30 minutes"], [3600, "1 hour"], [21600, "6 hours"], [43200, "12 hours"], [86400, "24 hours"], [0, "Resume now"]], "");
      picker.setAttribute("aria-label", `Snooze ${r.name}`);
      picker.addEventListener("change", async () => { try { await api("snooze", {id: r.id, seconds: Number(picker.value)}); await refresh(); } catch (e) { error("error", e.message); } });
      foot.append(picker); if (s.snooze_seconds > 0) foot.append(el("span", `${Math.ceil(s.snooze_seconds / 60)}m left`));
      card.append(foot); cards.append(card);
    }
  }
}

function predicateRow(p = {entity_id: "", value: "on"}, removable = false) {
  const row = el("div", undefined, "predicate"), grid = el("div", undefined, "grid");
  const entity = input("text", p.entity_id); entity.setAttribute("list", "entities"); entity.placeholder = "binary_sensor.night_lux";
  const operator = select([["eq", "Equals"], ["ne", "Does not equal"], ["gt", "Greater than"], ["lt", "Less than"], ["ge", "At least"], ["le", "At most"]], p.operator || "eq");
  grid.append(label("Entity", entity), label("Comparison", operator)); row.append(grid);
  const values = el("div", undefined, "grid"), value = input("text", typeof p.value === "string" ? p.value : JSON.stringify(p.value));
  const kind = select([["text", "Text / state"], ["json", "Number, boolean, or array (JSON)"]], typeof p.value === "string" ? "text" : "json");
  values.append(label("Expected value", value), label("Value type", kind)); row.append(values);
  const attribute = input("text", p.attribute || ""), tolerance = input("number", p.tolerance || 0); tolerance.min = 0; tolerance.step = "any";
  const extra = el("div", undefined, "grid"); extra.append(label("Attribute · optional", attribute), label("Numeric tolerance", tolerance)); row.append(extra);
  const unknown = select([["skip", "Skip sync"], ["match", "Treat as matching"], ["no_match", "Treat as not matching"]], p.unknown || "skip"); row.append(label("Unknown / unavailable", unknown));
  if (removable) row.append(button("Remove condition", () => row.remove()));
  row.read = () => {
    let expected = kind.value === "json" || !["eq", "ne"].includes(operator.value) ? JSON.parse(value.value) : value.value;
    const out = {entity_id: entity.value.trim(), operator: operator.value, value: expected, unknown: unknown.value};
    if (attribute.value.trim()) out.attribute = attribute.value.trim();
    if (Number(tolerance.value)) out.tolerance = Number(tolerance.value);
    return out;
  };
  return row;
}

const configs = {
  light: {states: ["on", "off"], fields: {brightness_pct: ["Brightness %", 1, 100], transition: ["Transition seconds", 0, 3600], color_temp_kelvin: ["Color temperature · Kelvin", 1000, 20000], rgb_color: ["RGB color", "color"]}},
  switch: {states: ["on", "off"], fields: {}}, input_boolean: {states: ["on", "off"], fields: {}},
  fan: {states: ["on", "off"], fields: {percentage: ["Speed %", 1, 100]}},
  humidifier: {states: ["on", "off"], fields: {humidity: ["Target humidity %", 0, 100], mode: ["Mode", "text"]}},
  climate: {states: ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"], fields: {temperature: ["Target temperature · HA units", -100, 100], target_temp_low: ["Low temperature", -100, 100], target_temp_high: ["High temperature", -100, 100], preset_mode: ["Preset mode", "text"]}},
  cover: {states: ["open", "closed"], fields: {position: ["Open position %", 0, 100]}},
  lock: {states: ["locked", "unlocked"], fields: {}},
  number: {states: [], fields: {value: ["Value", -1000000, 1000000]}}, input_number: {states: [], fields: {value: ["Value", -1000000, 1000000]}},
  select: {states: [], fields: {option: ["Option", "text"]}}, input_select: {states: [], fields: {option: ["Option", "text"]}}
};
function renderAction(id, action = {}) {
  const root = $(id); root.replaceChildren(); const config = configs[actionDomain];
  const mode = select([["basic", "Desired state"], ["custom", "Custom YAML service call"]], action.service || !config ? "custom" : "basic");
  root.append(label("Action mode", mode)); const basic = el("div", undefined, "action-fields"), custom = el("div", undefined, "action-fields"); root.append(basic, custom);
  let state, reads = [];
  if (config) {
    if (config.states.length) { state = select(config.states.map(x => [x, x]), action.state || config.states[0]); basic.append(label("State", state)); }
    const grid = el("div", undefined, "grid"); basic.append(grid);
    for (const [key, [name, min, max]] of Object.entries(config.fields)) {
      const color = min === "color", text = min === "text", old = action.data?.[key];
      const initial = color ? (old ? `#${old.map(x => x.toString(16).padStart(2, "0")).join("")}` : "#ffffff") : old ?? "";
      const field = input(color ? "color" : text ? "text" : "number", initial);
      if (!text && !color) { field.min = min; field.max = max; field.step = "any"; }
      if (color) {
        const wrap = el("div"), enabled = input("checkbox"); enabled.checked = old !== undefined;
        const c = label("Enforce RGB color", enabled); c.className = "check"; wrap.append(c, label(name, field)); wrap.dataset.key = key; grid.append(wrap);
        reads.push(data => { if (enabled.checked) data[key] = [1, 3, 5].map(i => parseInt(field.value.slice(i, i + 2), 16)); });
      } else {
        const wrap = label(name, field); wrap.dataset.key = key; grid.append(wrap); reads.push(data => { if (field.value !== "") data[key] = text ? field.value : Number(field.value); });
      }
    }
    const showFields = () => { for (const wrap of grid.children) wrap.hidden = state?.value === "off" && wrap.dataset.key !== "transition"; };
    state?.addEventListener("change", showFields); showFields();
  } else basic.append(el("p", "Use a custom service for this domain.", "hint"));
  const yaml = el("textarea"); yaml.rows = 12; yaml.spellcheck = false;
  yaml.value = JSON.stringify(action.service ? action : {service: `${actionDomain}.turn_on`, data: {}, satisfied: [{entity_id: "$target", value: "on"}]}, null, 2);
  custom.append(label("YAML · service, data, satisfied", yaml), el("p", "The service is called separately for each winning target. $target refers to that entity in satisfied predicates. Data cannot override targets.", "hint"));
  const toggle = () => { basic.hidden = mode.value !== "basic"; custom.hidden = !basic.hidden; }; mode.addEventListener("change", toggle); toggle();
  actionReaders[id] = () => {
    if (mode.value === "custom") return {yaml: yaml.value};
    if (!config) throw new Error("This domain needs a custom service.");
    const data = {}; reads.forEach(read => read(data));
    if (state?.value === "off") for (const key of Object.keys(data)) if (key !== "transition") delete data[key];
    return {action: {...(state ? {state: state.value} : {}), data}};
  };
}
function targetPicker() {
  const chips = $("selected"); chips.replaceChildren();
  for (const id of selected) chips.append(button(`${friendly(id)} ×`, () => { selected.delete(id); updateTargets(); }));
  const root = $("target-results"); root.replaceChildren(); const q = $("target-search").value.toLowerCase();
  const matches = entities.filter(e => `${e.entity_id} ${e.attributes?.friendly_name || ""}`.toLowerCase().includes(q)).slice(0, 100);
  for (const e of matches) {
    const i = input("checkbox"); i.checked = selected.has(e.entity_id);
    const span = el("span", friendly(e.entity_id)); span.append(el("small", `${e.entity_id} · ${e.state}`));
    const row = el("label", undefined, "check"); row.append(i, span); root.append(row);
    i.addEventListener("change", () => { if (i.checked) selected.add(e.entity_id); else selected.delete(e.entity_id); updateTargets(); });
  }
  if (!matches.length) root.append(el("p", "No matching entities. YAML can reference entities currently missing from HA.", "hint"));
}
function updateTargets() {
  const next = [...selected][0]?.split(".")[0] || "light";
  if (next !== actionDomain) { actionDomain = next; renderAction("desired"); renderAction("alternate", {state: configs[next]?.states.at(-1)}); }
  targetPicker();
}
function populate(rule) {
  if (!editing && rule.id) $("form").dataset.newId = rule.id;
  $("name").value = rule.name; $("priority").value = rule.priority ?? 50; $("enabled").checked = rule.enabled !== false;
  $("type").value = rule.trigger.type; $("source").replaceChildren(predicateRow(rule.trigger.type === "entity" ? rule.trigger : undefined));
  $("start").value = rule.trigger.start || "21:00"; $("end").value = rule.trigger.end || "01:00"; updateType();
  $("conditions").replaceChildren(...(rule.conditions || []).map(p => predicateRow(p, true)));
  selected = new Set(rule.targets); actionDomain = rule.targets[0]?.split(".")[0] || "light";
  renderAction("desired", rule.desired); renderAction("alternate", rule.alternate || {state: configs[actionDomain]?.states.at(-1)});
  $("inverted").checked = !!rule.alternate; $("alternate-box").hidden = !rule.alternate;
  for (const k of ["source", "target", "condition"]) $(`on-${k}`).checked = rule.reevaluate?.[k] !== false;
  $("on-interval").checked = (rule.reevaluate?.interval ?? 60) > 0; $("interval").value = rule.reevaluate?.interval || 60; $("interval").disabled = !$("on-interval").checked;
  $("target-search").value = ""; targetPicker();
}
function readForm() {
  const actions = {}, desired = actionReaders.desired();
  if (desired.yaml) actions.desired = desired.yaml;
  const rule = {id: editing?.id || $("form").dataset.newId, name: $("name").value.trim(), enabled: $("enabled").checked, priority: Number($("priority").value),
    trigger: $("type").value === "time" ? {type: "time", start: $("start").value, end: $("end").value} : {type: "entity", ...$("source").firstChild.read()},
    conditions: [...$("conditions").children].map(n => n.read()), targets: [...selected], desired: desired.action || {},
    reevaluate: {source: $("on-source").checked, target: $("on-target").checked, condition: $("on-condition").checked, interval: $("on-interval").checked ? Number($("interval").value) : 0}};
  if ($("inverted").checked) { const alt = actionReaders.alternate(); rule.alternate = alt.action || {}; if (alt.yaml) actions.alternate = alt.yaml; }
  return {yaml: JSON.stringify([rule]), actions};
}
function updateType() { $("source").hidden = $("type").value !== "entity"; $("times").hidden = !$("source").hidden; }
function setMode(raw) {
  rawMode = raw; $("fields").hidden = raw; $("yaml-box").hidden = !raw;
  $("name").required = !raw; $("priority").required = !raw;
  // Hidden form controls must not block YAML edits through browser validation.
  for (const n of $("fields").querySelectorAll("input,select,textarea")) n.disabled = raw;
  if (!raw) $("interval").disabled = !$("on-interval").checked;
}
async function openEditor(rule = null) {
  editing = rule; error("edit-error", ""); $("editor-title").textContent = rule ? "Edit sync" : "New sync"; $("delete").hidden = !rule;
  editRevision = snapshot.revision; $("form").dataset.newId = [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, "0")).join("");
  populate(rule || {name: "", trigger: {type: "entity", entity_id: "", value: "on"}, targets: [], desired: {state: "on"}});
  setMode(false); $("editor").showModal();
  try {
    entities = await api("entities"); $("entities").replaceChildren(...entities.map(e => { const o = el("option", `${friendly(e.entity_id)} · ${e.state}`); o.value = e.entity_id; return o; })); targetPicker();
  } catch (e) { error("edit-error", e.message); }
}
$("raw").addEventListener("click", async () => {
  if (rawMode) return;
  try { const result = await api("preview", {...readForm(), validate: false}); $("yaml").value = result.yaml; setMode(true); error("edit-error", ""); }
  catch (e) { error("edit-error", e.message); }
});
$("guided").addEventListener("click", async () => {
  if (!rawMode) return;
  try { const result = await api("preview", {yaml: $("yaml").value}); populate(result.rule); setMode(false); error("edit-error", ""); }
  catch (e) { error("edit-error", e.message); }
});
$("form").addEventListener("submit", async e => {
  e.preventDefault(); const b = e.submitter; b.disabled = true;
  try { await api("rule", {...(rawMode ? {yaml: $("yaml").value} : readForm()), id: editing?.id, revision: editRevision}); $("editor").close(); await refresh(); }
  catch (e) { error("edit-error", e.message); } finally { b.disabled = false; }
});
$("delete").addEventListener("click", async () => {
  if (!confirm(`Delete “${editing.name}”? This removes its saved definition.`)) return;
  try { await api("delete", {id: editing.id, revision: editRevision}); $("editor").close(); await refresh(); } catch (e) { error("edit-error", e.message); }
});
$("new").addEventListener("click", () => openEditor()); $("close").addEventListener("click", () => $("editor").close());
$("type").addEventListener("change", updateType);
$("add-condition").addEventListener("click", () => $("conditions").append(predicateRow(undefined, true)));
$("target-search").addEventListener("input", targetPicker);
$("inverted").addEventListener("change", () => $("alternate-box").hidden = !$("inverted").checked);
$("on-interval").addEventListener("change", () => $("interval").disabled = !$("on-interval").checked);
async function poll() {
  try {
    await refresh();
    if (snapshot.connected && !entities.length) { entities = await api("entities"); if (!$("overview").contains(document.activeElement)) renderOverview(); }
  } catch (e) { error("error", e.message); }
  setTimeout(poll, 3000);
}
poll();
