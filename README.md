# State Sync

A Home Assistant app that maintains desired entity states. Define **when** a state should be enforced, optional conditions, and one or more targets. Events, time boundaries, and intervals request reconciliation; service calls happen only when the desired state differs.

State Sync complements Home Assistant automations. It has no action sequences, delays, notification workflow, or template execution.

## Install

Requires Home Assistant with Supervisor (normally Home Assistant OS), on `aarch64` or `amd64`.

1. In **Settings → Apps**, open the app store’s repository menu and add `https://github.com/inventor7777/State-Sync-HA` after these files are pushed to GitHub.
2. Install **State Sync**, review the app options, and start it.
3. Open its web UI through Home Assistant ingress and create a sync.

Supervisor builds the container locally from the Dockerfile, including Python and its dependencies. There is no prebuilt State Sync image, registry account, or local Docker installation required on your development computer. Builds still need internet access to obtain the base image and Alpine packages.

For testing unpushed changes, copy `state_sync/` to `/addons/state_sync/` on the HA machine, reload the app store, and install it from **Local apps**. Rebuild the app after changing source files.

This first version is **experimental**. Automated tests use a simulated HA server; a real Supervisor build and physical devices still need validation.

## Behavior

- Select multiple explicit entity targets, such as both porch lights. Each receives the same desired state.
- Priority is resolved **per target entity**. Highest priority wins; ties use saved rule order. The winning rule supplies that entity’s entire desired state. Lower-priority attributes are not merged.
- A condition gates both the normal and alternate desired state. Every condition must match.
- Sources/conditions that are missing, unknown, or unavailable skip the sync by default, including inversion. Each predicate can instead treat that case as matching or not matching. An unavailable target is always skipped.
- Time windows use HA’s timezone, include their start, exclude their end, and can cross midnight. Equal start/end times are rejected. Time windows repeat daily; no weekday/calendar system.
- Startup, reconnection, valid config edits, and snooze expiry reconcile immediately. Time rules reevaluate each minute, including boundaries. Source, target, condition, and interval reevaluation can be individually disabled.
- When a rule requests reevaluation, all active rules sharing its targets participate in arbitration. This prevents an event for a lower-priority rule from bypassing a higher-priority rule.
- Service calls wait at least `retry_seconds` before checking for another correction. Light transitions extend this wait. Failed calls back off up to five minutes. Pending corrections are rechecked against current conditions and priority before retrying.
- No active rule means no action: State Sync does not restore an earlier state after a sync stops enforcing. The optional alternate state supplies that behavior explicitly.
- Disable switches persist in YAML. Snoozes (5m/30m/1h/6h/12h/24h) are in memory and clear on restart. A different active sync can still control a snoozed sync’s targets.

Basic adapters support lights, switches, input booleans, fans, humidifiers, climate, covers, locks, numbers/input numbers, and selects/input selects. Other domains use custom service calls. The editor shows fields for each domain; individual integrations may not support every field or value. HA validates service calls, and failures appear in the overview.

## Configuration

One-time options belong in Home Assistant’s app configuration:

```yaml
log_level: info
retry_seconds: 10
accent_light: '#2563eb'
accent_dark: '#93b4ff'
```

Automatic light/dark mode follows the browser’s system preference. The two accent options customize those palettes. Raise `retry_seconds` for devices that take longer to report their final state.

Sync definitions live in `syncs.yaml` under the app’s config directory (`/app_configs/<repository>_state_sync/` from HA; `/config/syncs.yaml` inside the app). Both the form and YAML editor update that file atomically. Manual edits reload within about a second. Invalid files pause all enforcement until fixed; the app never overwrites them. UI saves reject stale edits instead of replacing someone else’s changes. YAML comments/formatting are normalized when the UI saves; avoid editing manually at the exact same time as saving in the UI.

Example with two lights, a manual gate, and inversion:

```yaml
- id: porch_night
  name: Porch lights at night
  enabled: true
  priority: 50
  trigger:
    type: time
    start: '21:00'
    end: '01:00'
  conditions:
    - entity_id: switch.manual
      value: 'off'
      unknown: skip
  targets:
    - light.porch_a
    - light.porch_b
  desired:
    state: 'on'
    data:
      brightness_pct: 50
      transition: 2
  alternate:
    state: 'off'
  reevaluate:
    source: true
    target: true
    condition: true
    interval: 60
```

For an entity sync, replace the trigger with:

```yaml
trigger:
  type: entity
  entity_id: binary_sensor.tempest_night_lux
  value: 'on'
  unknown: skip
```

Predicates support `operator: eq|ne|gt|lt|ge|le`, optional `attribute`, and numeric `tolerance`. State strings such as `'on'` and `'off'` must be quoted in YAML. Numeric comparisons use numeric values. Missing attributes skip the sync by default. `interval: 0` disables interval reevaluation.

Custom actions replace `desired` (or `alternate`) with one service and an explicit list of already-satisfied predicates:

```yaml
desired:
  service: media_player.volume_set
  data:
    volume_level: 0.3
  satisfied:
    - entity_id: $target
      attribute: volume_level
      value: 0.3
      tolerance: 0.01
```

The service receives only the individual winning target. `data` cannot override entity/device/area targets. All `satisfied` predicates must match to suppress the call; if one is unknown with the default policy, the target is skipped. Custom services must represent an idempotent desired state; a toggle, notification, or one-shot action will not behave as a sync. There is no Jinja or arbitrary HA automation YAML execution.

Basic data fields: light (`brightness_pct`, `transition`, `rgb_color`, `color_temp_kelvin`); fan (`percentage`); humidifier (`humidity`, `mode`); climate (`temperature` or `target_temp_low`/`target_temp_high`, `preset_mode`); cover (`position`); number (`value`); select (`option`). Climate `state` is its HVAC mode, not whether it is currently heating/cooling. Light brightness compares within 2/255, RGB within 3/channel, Kelvin within 100; cover/fan/humidity within 1; temperature within 0.1. Transitions are call parameters, not persistent attributes.

Select individual member entities rather than a group and its members: arbitration operates on explicit entity IDs, without expanding groups, areas, or devices. Mixed domains work only when the same desired-state fields make sense for every target; otherwise use separate syncs.

## Development

Python 3.11+; no frontend bundler or database.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r state_sync/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
node --check state_sync/app/static/app.js
```

For a local UI preview without HA credentials:

```sh
STATE_SYNC_DEV=1 STATE_SYNC_FILE=/tmp/state-sync-dev/syncs.yaml \
  PYTHONPATH=state_sync .venv/bin/python -m app
```

Open `http://127.0.0.1:8099`. The engine pauses while HA is disconnected. For local testing against your own HA, additionally set `HA_API=http://<HA-host>:8123/api` and `HA_TOKEN` to a long-lived access token. Such a connection **will enforce your saved syncs**. Keep the token out of source control.

Production uses `SUPERVISOR_TOKEN`, `http://supervisor/core/api`, and `ws://supervisor/core/websocket` automatically. The ingress server accepts only Supervisor’s ingress proxy. Development mode binds to localhost and accepts loopback clients only. Credentials stay in the backend. No host ports, privileged access, or writes to HA’s internal configuration are needed.

Domain adapters and predicate evaluation live in `state_sync/app/model.py`; the serialized loop is in `engine.py`, HA transport in `ha.py`, atomic file persistence in `store.py`, and HTTP/UI in `__main__.py` and `static/`.
