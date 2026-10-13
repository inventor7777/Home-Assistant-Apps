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

const overviewCards = new Map();
let overviewLayout = "", loadingEntities = false;
function connectionState(connected) {
  $("app-title").classList.toggle("disconnected", !connected);
  $("app-title").title = connected ? "" : "Home Assistant connection unavailable";
  $("app-title").setAttribute("aria-label", connected ? "State Sync" : "State Sync — disconnected");
}
function applySnapshot(data) {
  snapshot = data;
  connectionState(snapshot.connected);
  error("error", snapshot.error && snapshot.connected ? snapshot.error : "");
  renderOverview();
  if (snapshot.connected && !entities.length && !loadingEntities) {
    loadingEntities = true;
    api("entities").then(data => { entities = data; renderOverview(); })
      .catch(e => error("error", e.message)).finally(() => { loadingEntities = false; });
  }
}
async function refresh() { applySnapshot(await api("snapshot")); }
function renderOverview() {
  const root = $("overview");
  const layout = JSON.stringify(snapshot.rules.map(r => [r.id, r.trigger.type]));
  if (layout !== overviewLayout) {
    overviewLayout = layout; root.replaceChildren(); overviewCards.clear();
    if (!snapshot.rules.length) root.append(el("div", "No syncs yet. Create one to keep your devices in the right state.", "empty"));
    for (const type of ["entity", "time"]) {
      const rules = snapshot.rules.filter(r => r.trigger.type === type);
      if (!rules.length) continue;
      const heading = el("div", undefined, "section-head"); heading.append(el("h2", type === "entity" ? "Entity syncs" : "Time syncs"), el("small", `${rules.length} sync${rules.length === 1 ? "" : "s"}`)); root.append(heading);
      const cards = el("div", undefined, "cards"); root.append(cards);
      for (const r of rules) {
        const current = () => snapshot.rules.find(rule => rule.id === r.id);
        const edit = () => openEditor(current());
        const card = el("article", undefined, "card"), head = el("div", undefined, "card-head");
        const title = button(r.name, edit, "card-title");
        const enabled = check("", r.enabled !== false, async event => {
          const i = event.target; i.disabled = true;
          try { await api("rule", {id: r.id, revision: snapshot.revision, yaml: JSON.stringify([{...current(), enabled: i.checked}])}); await refresh(); }
          catch (e) { i.checked = current().enabled !== false; error("error", e.message); } finally { i.disabled = false; }
        });
        const toggle = enabled.querySelector("input"); toggle.setAttribute("role", "switch"); toggle.className = "switch";
        head.append(title, enabled); card.append(head);
        const description = el("p", undefined, "description"), badge = el("span", undefined, "badge"), statuses = el("ul", undefined, "target-status");
        card.append(description, badge, statuses);
        const picker = select([["", "Snooze…"], [300, "5 minutes"], [1800, "30 minutes"], [3600, "1 hour"], [21600, "6 hours"], [43200, "12 hours"], [86400, "24 hours"], [0, "Resume now"]], "");
        picker.addEventListener("change", async () => {
          picker.disabled = true;
          try { await api("snooze", {id: r.id, seconds: Number(picker.value)}); await refresh(); }
          catch (e) { error("error", e.message); }
          finally { picker.value = ""; picker.disabled = false; }
        });
        const remaining = el("span"), foot = el("div", undefined, "card-foot");
        const editButton = button("Edit", edit); foot.append(picker, remaining, editButton);
        card.append(foot); cards.append(card);
        overviewCards.set(r.id, {title, toggle, description, badge, statuses, picker, remaining, editButton});
      }
    }
  }
  for (const r of snapshot.rules) {
    const c = overviewCards.get(r.id), s = snapshot.status[r.id] || {reason: "Waiting for evaluation", targets: {}};
    c.title.textContent = r.name;
    c.toggle.setAttribute("aria-label", `Enable ${r.name}`);
    if (!c.toggle.disabled) c.toggle.checked = r.enabled !== false;
    const when = r.trigger.type === "time" ? `${r.trigger.start} – ${r.trigger.end}` : `${friendly(r.trigger.entity_id)} ${r.trigger.operator || "eq"} ${JSON.stringify(r.trigger.value)}`;
    c.description.textContent = `${when} → ${r.targets.map(friendly).join(", ")}`;
    c.badge.textContent = s.reason;
    c.statuses.replaceChildren(...Object.entries(s.targets).map(([entity, reason]) => el("li", `${friendly(entity)}: ${reason}`)));
    c.picker.setAttribute("aria-label", `Snooze ${r.name}`);
    c.remaining.textContent = s.snooze_seconds > 0 ? `${Math.ceil(s.snooze_seconds / 60)}m left` : "";
    c.remaining.hidden = !s.snooze_seconds;
    c.editButton.setAttribute("aria-label", `Edit ${r.name}`);
  }
}

function inheritTheme() {
  try {
    if (window.parent === window) return;
    const host = window.parent.document.querySelector("home-assistant");
    if (!host) return;
    const copy = () => {
      const styles = window.parent.getComputedStyle(host);
      const colors = {bg: "primary-background-color", surface: "card-background-color", text: "primary-text-color", muted: "secondary-text-color", line: "divider-color", soft: "secondary-background-color", accent: "primary-color", "on-accent": "text-primary-color", error: "error-color"};
      for (const [local, ha] of Object.entries(colors)) {
        const value = styles.getPropertyValue(`--${ha}`).trim();
        if (value && CSS.supports("color", value)) document.documentElement.style.setProperty(`--${local}`, value);
        else document.documentElement.style.removeProperty(`--${local}`);
      }
      const background = styles.getPropertyValue("--primary-background-color").trim();
      if (background) document.documentElement.style.colorScheme = host.hass?.themes?.darkMode ? "dark" : "light";
    };
    copy();
    // Ingress is same-origin; HA sets active theme variables on the document root.
    new MutationObserver(copy).observe(window.parent.document.documentElement, {attributes: true, attributeFilter: ["style"]});
  } catch (_) { /* Standalone or cross-origin embeds keep the local palette. */ }
}

function entityPicker(ids, changed, multiple = false) {
  const root = el("div", undefined, "entity-picker"), chips = el("div", undefined, "chips");
  const panel = el("div", undefined, "entity-search"); panel.hidden = true;
  const search = input("search"); search.placeholder = "Search name or entity ID"; search.autocomplete = "off";
  const results = el("div", undefined, "picker");
  const add = button("+ Add entity", () => { panel.hidden = false; add.hidden = true; search.focus(); });
  add.setAttribute("aria-expanded", "false");
  const close = () => { panel.hidden = true; add.hidden = false; search.value = ""; add.setAttribute("aria-expanded", "false"); add.focus(); };
  const render = () => {
    chips.replaceChildren();
    for (const id of ids) {
      const chip = button(`${friendly(id)} ×`, () => { ids.delete(id); render(); changed(); });
      chip.setAttribute("aria-label", `Remove ${friendly(id)}`); chip.title = id; chips.append(chip);
    }
    add.textContent = ids.size && !multiple ? "Change entity" : "+ Add entity";
  };
  const find = () => {
    results.replaceChildren();
    const q = search.value.trim().toLowerCase();
    if (q.length < 2) { results.append(el("p", "Type at least 2 characters to search.", "hint")); return; }
    const matches = entities.filter(e => !ids.has(e.entity_id) && `${e.entity_id} ${e.attributes?.friendly_name || ""}`.toLowerCase().includes(q));
    for (const e of matches.slice(0, 30)) {
      const pick = button(friendly(e.entity_id), () => {
        if (!multiple) ids.clear(); ids.add(e.entity_id); render(); changed();
        if (multiple) { find(); search.focus(); } else close();
      }, "entity-result");
      pick.append(el("small", `${e.entity_id} · ${e.state}`)); results.append(pick);
    }
    if (!matches.length) results.append(el("p", "No matching entities.", "hint"));
    if (matches.length > 30) results.append(el("p", `${matches.length} matches — refine your search.`, "hint"));
  };
  search.addEventListener("input", find);
  search.addEventListener("keydown", event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); } });
  add.addEventListener("click", () => { add.setAttribute("aria-expanded", "true"); find(); });
  panel.append(label("Search entities", search), button(multiple ? "Done" : "Cancel", close), results);
  root.append(chips, add, panel); render();
  return root;
}

function predicateRow(p = {entity_id: "", value: "on"}, removable = false) {
  const row = el("div", undefined, "predicate"), grid = el("div", undefined, "grid");
  const ids = new Set(p.entity_id ? [p.entity_id] : []);
  row.append(entityPicker(ids, () => {}));
  const operator = select([["eq", "Equals"], ["ne", "Does not equal"], ["gt", "Greater than"], ["lt", "Less than"], ["ge", "At least"], ["le", "At most"]], p.operator || "eq");
  const value = input("text", typeof p.value === "string" ? p.value : JSON.stringify(p.value));
  const kind = select([["text", "Text / state"], ["json", "Number, boolean, or array (JSON)"]], typeof p.value === "string" ? "text" : "json");
  grid.append(label("Comparison", operator), label("Expected value", value)); row.append(grid);
  const advanced = el("details", undefined, "advanced"); advanced.append(el("summary", "Advanced options"));
  advanced.open = !!(p.attribute || p.tolerance || p.unknown && p.unknown !== "skip" || typeof p.value !== "string");
  advanced.append(label("Value type", kind));
  const attribute = input("text", p.attribute || ""), tolerance = input("number", p.tolerance || 0); tolerance.min = 0; tolerance.step = "any";
  const extra = el("div", undefined, "grid"); extra.append(label("Attribute · optional", attribute), label("Numeric tolerance", tolerance)); advanced.append(extra);
  const unknown = select([["skip", "Skip sync"], ["match", "Treat as matching"], ["no_match", "Treat as not matching"]], p.unknown || "skip"); advanced.append(label("Unknown / unavailable", unknown)); row.append(advanced);
  if (removable) row.append(button("Remove condition", () => row.remove()));
  row.read = () => {
    let expected = kind.value === "json" || !["eq", "ne"].includes(operator.value) ? JSON.parse(value.value) : value.value;
    const out = {entity_id: [...ids][0] || "", operator: operator.value, value: expected, unknown: unknown.value};
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
    const attributes = el("details", undefined, "advanced"); attributes.append(el("summary", "State attributes"));
    attributes.open = Object.keys(action.data || {}).length > 0;
    const grid = el("div", undefined, "grid"); attributes.append(grid); basic.append(attributes);
    attributes.hidden = !Object.keys(config.fields).length;
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
function updateTargets() {
  const next = [...selected][0]?.split(".")[0] || "light";
  if (next !== actionDomain) { actionDomain = next; renderAction("desired"); renderAction("alternate", {state: configs[next]?.states.at(-1)}); }
}
function populate(rule) {
  if (!editing && rule.id) $("form").dataset.newId = rule.id;
  $("name").value = rule.name ?? ""; $("priority").value = rule.priority ?? 50; $("enabled").checked = rule.enabled !== false;
  $("type").value = rule.trigger.type; $("source").replaceChildren(predicateRow(rule.trigger.type === "entity" ? rule.trigger : undefined));
  $("start").value = rule.trigger.start || "21:00"; $("end").value = rule.trigger.end || "01:00"; updateType();
  $("conditions").replaceChildren(...(rule.conditions || []).map(p => predicateRow(p, true)));
  selected = new Set(rule.targets); actionDomain = rule.targets[0]?.split(".")[0] || "light";
  renderAction("desired", rule.desired); renderAction("alternate", rule.alternate || {state: configs[actionDomain]?.states.at(-1)});
  $("inverted").checked = !!rule.alternate; $("alternate-box").hidden = !rule.alternate;
  for (const k of ["source", "target", "condition"]) $(`on-${k}`).checked = rule.reevaluate?.[k] !== false;
  $("on-interval").checked = (rule.reevaluate?.interval ?? 60) > 0; $("interval").value = rule.reevaluate?.interval || 60; $("interval").disabled = !$("on-interval").checked;
  $("targets").replaceChildren(entityPicker(selected, updateTargets, true));
  $("sync-options").open = false;
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
function updateType() { $("source").hidden = $("type").value !== "entity"; $("times").hidden = !$("source").hidden; $("time-hint").hidden = $("times").hidden; }
function setMode(raw) {
  rawMode = raw; $("fields").hidden = raw; $("yaml-box").hidden = !raw;
  $("raw").setAttribute("aria-pressed", String(raw)); $("guided").setAttribute("aria-pressed", String(!raw));
  $("name").required = !raw; $("priority").required = !raw;
  // Hidden form controls must not block YAML edits through browser validation.
  for (const n of $("fields").querySelectorAll("input,select,textarea")) n.disabled = raw;
  if (!raw) $("interval").disabled = !$("on-interval").checked;
}
async function openEditor(rule = null) {
  editing = rule; error("edit-error", ""); $("editor-title").textContent = rule ? "Edit Sync" : "New Sync"; $("delete").hidden = !rule;
  editRevision = snapshot.revision; $("form").dataset.newId = [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, "0")).join("");
  populate(rule || {name: "", trigger: {type: "entity", entity_id: "", value: "on"}, targets: [], desired: {state: "on"}});
  setMode(false); $("editor").showModal();
  try {
    entities = await api("entities");
  } catch (e) { error("edit-error", e.message); }
}
$("raw").addEventListener("click", async () => {
  if (rawMode) return;
  try { const result = await api("preview", {...readForm(), validate: false}); $("yaml").value = result.yaml; setMode(true); error("edit-error", ""); }
  catch (e) { error("edit-error", e.message); }
});
$("guided").addEventListener("click", async () => {
  if (!rawMode) return;
  try { const result = await api("preview", {yaml: $("yaml").value, draft: true}); populate(result.rule); setMode(false); error("edit-error", ""); }
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
$("inverted").addEventListener("change", () => $("alternate-box").hidden = !$("inverted").checked);
$("on-interval").addEventListener("change", () => $("interval").disabled = !$("on-interval").checked);
inheritTheme();
connectionState(false);
const stream = new EventSource("api/events");
stream.onmessage = event => applySnapshot(JSON.parse(event.data));
stream.onerror = () => connectionState(false);
