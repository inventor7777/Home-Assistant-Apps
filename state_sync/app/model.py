"""Validated YAML rules and small, explicit domain adapters."""

import json
import math
import re
from datetime import time

ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
POWER = {"light", "switch", "input_boolean", "fan", "humidifier"}
FIELDS = {
    "light": {"brightness_pct", "transition", "rgb_color", "color_temp_kelvin"},
    "fan": {"percentage"},
    "humidifier": {"humidity", "mode"},
    "climate": {"temperature", "target_temp_low", "target_temp_high", "preset_mode"},
    "cover": {"position"},
    "number": {"value"}, "input_number": {"value"},
    "select": {"option"}, "input_select": {"option"},
    "lock": set(), "switch": set(), "input_boolean": set(),
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def mapping(value, allowed, where):
    require(isinstance(value, dict), f"{where} must be a mapping")
    require(all(isinstance(k, str) for k in value), f"{where} keys must be strings")
    require(not (value.keys() - allowed), f"Unexpected fields in {where}: {value.keys() - allowed}")


def number(value, low=-1e6, high=1e6):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def clock(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{2}:\d{2}", value), "Time must be HH:MM")
    return time.fromisoformat(value)


def validate_predicate(p, target=False, draft=False):
    mapping(p, {"entity_id", "attribute", "operator", "value", "unknown", "tolerance"}, "predicate")
    entity = p.get("entity_id", "")
    require(isinstance(entity, str) and (ENTITY.fullmatch(entity) or target and entity == "$target" or draft and entity == ""), "Invalid predicate entity_id")
    require(p.get("operator", "eq") in {"eq", "ne", "gt", "lt", "ge", "le"}, "Invalid operator")
    require("value" in p and isinstance(p["value"], (str, int, float, bool, list)), "Predicate needs a value")
    require(p.get("unknown", "skip") in {"skip", "match", "no_match"}, "Invalid unknown policy")
    require("attribute" not in p or isinstance(p["attribute"], str), "Attribute must be a string")
    require(number(p.get("tolerance", 0), 0), "Tolerance must be nonnegative")
    if p.get("operator", "eq") not in {"eq", "ne"}:
        require(number(p["value"]), "Numeric comparison needs a numeric value")


def validate_action(action, targets):
    mapping(action, {"state", "data", "service", "satisfied"}, "desired state")
    data = action.get("data", {})
    require(isinstance(data, dict) and all(isinstance(k, str) for k in data), "Action data must be a mapping")
    if "service" in action:
        require(isinstance(action["service"], str) and ENTITY.fullmatch(action["service"]), "Service must be domain.service")
        require("state" not in action, "Custom services use satisfied predicates, not state")
        require(not ({"entity_id", "device_id", "area_id", "target"} & data.keys()), "Targets belong in the sync target list")
        require(isinstance(action.get("satisfied"), list) and action["satisfied"], "Custom service needs satisfied predicates")
        for p in action["satisfied"]:
            validate_predicate(p, target=True)
        return
    require("satisfied" not in action, "Satisfied predicates are for custom services")
    for entity in targets:
        domain = entity.split(".")[0]
        require(domain in FIELDS, f"Use a custom service for {domain}")
        require(not (data.keys() - FIELDS[domain]), f"Unsupported {domain} fields: {data.keys() - FIELDS[domain]}")
        state = action.get("state")
        if domain in POWER:
            require(state in {"on", "off"}, f"{domain} needs on/off state (quote these in YAML)")
            require(state != "off" or not (data.keys() - {"transition"}), "Off only accepts transition")
        elif domain == "cover":
            require(state in {"open", "closed"}, "Cover state must be open/closed")
            require("position" not in data or state == ("closed" if data["position"] == 0 else "open"), "Position and cover state conflict")
        elif domain == "lock":
            require(state in {"locked", "unlocked"}, "Lock state must be locked/unlocked")
        elif domain == "climate":
            require(state in {"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"}, "Invalid HVAC mode")
            require(not ("temperature" in data and ({"target_temp_low", "target_temp_high"} & data.keys())), "Choose one setpoint or a low/high range")
            require(("target_temp_low" in data) == ("target_temp_high" in data), "Temperature range needs low and high")
            require(not data or state != "off", "Off climate state cannot have setpoints/presets")
        else:
            require(state is None, "Number/select states use data.value/data.option")
            require("value" in data if domain in {"number", "input_number"} else "option" in data, "Missing value/option")
    limits = {"brightness_pct": (1, 100), "percentage": (1, 100), "position": (0, 100), "humidity": (0, 100), "transition": (0, 3600), "color_temp_kelvin": (1000, 20000)}
    for key, value in data.items():
        if key in limits:
            require(number(value, *limits[key]), f"Invalid {key}")
        elif key in {"temperature", "target_temp_low", "target_temp_high", "value"}:
            require(number(value), f"Invalid {key}")
        elif key == "rgb_color":
            require(isinstance(value, list) and len(value) == 3 and all(type(v) is int and 0 <= v <= 255 for v in value), "RGB must have three integers 0–255")
        else:
            require(isinstance(value, str) and value, f"Invalid {key}")
    require(not ("rgb_color" in data and "color_temp_kelvin" in data), "Choose RGB or color temperature")
    if "target_temp_low" in data:
        require(data["target_temp_low"] <= data["target_temp_high"], "Low temperature exceeds high")


def _validate_rules(rules, draft=False):
    require(isinstance(rules, list) and len(rules) <= 500, "Syncs must be a list (maximum 500)")
    # JSON compatibility also rejects recursive YAML aliases and nonfinite values.
    try:
        json.dumps(rules, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Syncs must contain finite JSON-compatible values") from exc
    ids = set()
    for r in rules:
        mapping(r, {"id", "name", "enabled", "priority", "trigger", "conditions", "targets", "desired", "alternate", "reevaluate"}, "sync")
        require(isinstance(r.get("id"), str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", r["id"]), "Invalid sync ID")
        require(r["id"] not in ids, "Duplicate sync ID")
        ids.add(r["id"])
        name = r.get("name", "") if draft else r.get("name")
        require(isinstance(name, str) and (draft or len(name) > 0) and len(name) <= 200, "Sync needs a name")
        require(type(r.get("enabled", True)) is bool, "Enabled must be boolean")
        require(type(r.get("priority", 50)) is int and 0 <= r.get("priority", 50) <= 100, "Priority must be 0–100")
        t = r.get("trigger")
        require(isinstance(t, dict), "Sync needs a trigger")
        if t.get("type") == "entity":
            mapping(t, {"type", "entity_id", "attribute", "operator", "value", "unknown", "tolerance"}, "entity trigger")
            validate_predicate({k: v for k, v in t.items() if k != "type"}, draft=draft)
        else:
            mapping(t, {"type", "start", "end"}, "time trigger")
            require(t.get("type") == "time", "Trigger type must be entity/time")
            require(clock(t.get("start")) != clock(t.get("end")), "Start and end must differ")
        conditions = r.get("conditions", [])
        require(isinstance(conditions, list), "Conditions must be a list")
        for p in conditions:
            validate_predicate(p, draft=draft)
        targets = r.get("targets")
        require(isinstance(targets, list) and (draft or targets) and len(targets) <= 100, "Choose 1–100 targets")
        require(all(isinstance(e, str) and ENTITY.fullmatch(e) for e in targets), "Invalid target entity ID")
        require(len(set(targets)) == len(targets), "Duplicate targets")
        validate_action(r.get("desired"), targets)
        if "alternate" in r:
            validate_action(r["alternate"], targets)
        opts = r.get("reevaluate", {})
        mapping(opts, {"source", "target", "condition", "interval"}, "reevaluate")
        for key in {"source", "target", "condition"}:
            require(type(opts.get(key, True)) is bool, f"{key} must be boolean")
        require(type(opts.get("interval", 60)) is int and (opts.get("interval", 60) == 0 or 5 <= opts.get("interval", 60) <= 86400), "Interval must be 0 (disabled), or 5–86400 seconds")
    return rules


def validate_rules(rules, draft=False):
    try:
        return _validate_rules(rules, draft=draft)
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError("Invalid sync field type") from exc


def equal(actual, desired, tolerance=0):
    if isinstance(actual, str) and type(desired) in (int, float):
        try:
            actual = float(actual)
        except ValueError:
            return False
    if type(actual) in (int, float) and type(desired) in (int, float):
        return math.isfinite(actual) and abs(actual - desired) <= tolerance
    if isinstance(actual, (list, tuple)) and isinstance(desired, (list, tuple)):
        return len(actual) == len(desired) and all(equal(a, b, tolerance) for a, b in zip(actual, desired))
    return actual == desired


def predicate(p, states, target=None):
    entity = target if p["entity_id"] == "$target" else p["entity_id"]
    s = states.get(entity)
    value = None if not s else (s.get("attributes", {}).get(p["attribute"]) if "attribute" in p else s["state"])
    if not s or s["state"] in {"unknown", "unavailable"} or value is None:
        return {"skip": None, "match": True, "no_match": False}[p.get("unknown", "skip")]
    op = p.get("operator", "eq")
    desired = p["value"]
    if op in {"eq", "ne"}:
        result = equal(value, desired, p.get("tolerance", 0))
        return result if op == "eq" else not result
    try:
        a, b = float(value), float(desired)
        if not math.isfinite(a):
            return None
        return {"gt": a > b, "lt": a < b, "ge": a >= b, "le": a <= b}[op]
    except (ValueError, TypeError):
        return None


def active_action(rule, states, now):
    if not rule.get("enabled", True):
        return None, "Disabled"
    if any(predicate(p, states) is not True for p in rule.get("conditions", [])):
        return None, "Conditions not met or unknown"
    t = rule["trigger"]
    if t["type"] == "entity":
        match = predicate(t, states)
    else:
        start, end, current = clock(t["start"]), clock(t["end"]), now.time().replace(tzinfo=None)
        match = start <= current < end if start < end else current >= start or current < end
    if match is None:
        return None, "Source unknown — skipped"
    if match:
        return rule["desired"], "Enforcing"
    return (rule["alternate"], "Enforcing alternate") if "alternate" in rule else (None, "Inactive")


def action_commands(entity, action, states):
    """Only return service calls for differences in the declared desired state."""
    state = states.get(entity)
    if not state or state["state"] in {"unknown", "unavailable"}:
        return None
    if "service" in action:
        matches = [predicate(p, states, entity) for p in action["satisfied"]]
        if None in matches:
            return None
        return [] if all(matches) else [(action["service"], action.get("data", {}))]
    domain = entity.split(".")[0]
    actual, attrs = state["state"], state.get("attributes", {})
    wanted, data = action.get("state"), action.get("data", {})
    if domain == "light":
        matches = actual == wanted
        if wanted == "on":
            for k, v in data.items():
                if k == "transition":
                    continue
                attribute, value, tolerance = k, v, 0
                if k == "brightness_pct":
                    attribute, value, tolerance = "brightness", round(v * 255 / 100), 2
                elif k == "rgb_color":
                    tolerance = 3
                elif k == "color_temp_kelvin":
                    tolerance = 100
                matches = matches and equal(attrs.get(attribute), value, tolerance)
        payload = data if wanted == "on" else {k: v for k, v in data.items() if k == "transition"}
        return [] if matches else [(f"light.turn_{wanted}", payload)]
    if domain in POWER:
        commands = [] if actual == wanted else [(f"{domain}.turn_{wanted}", {})]
        if wanted == "on":
            for field, value in data.items():
                if not equal(attrs.get(field), value, 1 if field in {"percentage", "humidity"} else 0):
                    service = {"percentage": "set_percentage", "humidity": "set_humidity", "mode": "set_mode"}[field]
                    commands.append((f"{domain}.{service}", {field: value}))
        return commands
    if domain == "cover":
        if "position" in data:
            return [] if actual == wanted and equal(attrs.get("current_position"), data["position"], 1) else [("cover.set_cover_position", data)]
        return [] if actual == wanted else [("cover.close_cover" if wanted == "closed" else "cover.open_cover", {})]
    if domain == "lock":
        return [] if actual == wanted else [("lock.lock" if wanted == "locked" else "lock.unlock", {})]
    if domain == "climate":
        commands = [] if actual == wanted else [("climate.set_hvac_mode", {"hvac_mode": wanted})]
        temperatures = {k: v for k, v in data.items() if k != "preset_mode"}
        if temperatures and any(not equal(attrs.get(k), v, 0.1) for k, v in temperatures.items()):
            commands.append(("climate.set_temperature", temperatures))
        if "preset_mode" in data and attrs.get("preset_mode") != data["preset_mode"]:
            commands.append(("climate.set_preset_mode", {"preset_mode": data["preset_mode"]}))
        return commands
    if domain in {"number", "input_number"}:
        try:
            matches = equal(float(actual), data["value"], 0.001)
        except ValueError:
            matches = False
        return [] if matches else [(f"{domain}.set_value", data)]
    return [] if actual == data["option"] else [(f"{domain}.select_option", data)]


def event_affects(rule, entity):
    opts = rule.get("reevaluate", {})
    t = rule["trigger"]
    return (
        opts.get("source", True) and t["type"] == "entity" and entity == t["entity_id"]
        or opts.get("target", True) and entity in rule["targets"]
        or opts.get("condition", True) and any(entity == p["entity_id"] for p in rule.get("conditions", []))
        or opts.get("condition", True) and any(
            p["entity_id"] != "$target" and entity == p["entity_id"] and entity not in rule["targets"]
            for a in [rule["desired"], rule.get("alternate", {})]
            for p in a.get("satisfied", [])
        )
    )
