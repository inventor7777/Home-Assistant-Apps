import asyncio
import copy
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import aiohttp
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "state_sync"))
from app.__main__ import create_app, HA, ENGINE
from app.engine import Engine
from app.ha import HomeAssistant
from app.model import action_commands, active_action, event_affects, predicate, validate_rules
from app.store import Conflict, Store, parse


def state(value, **attrs):
    return {"state": value, "attributes": attrs}


def rule(ident="porch", priority=50, **overrides):
    return {"id": ident, "name": ident, "priority": priority,
            "trigger": {"type": "entity", "entity_id": "binary_sensor.night", "value": "on"},
            "desired": [{"entity_id": "light.porch_a", "state": "on"},
                        {"entity_id": "light.porch_b", "state": "on"}], **overrides}


class ModelTests(unittest.TestCase):
    def test_per_entity_states_validate_domains_duplicates_and_custom_dependencies(self):
        r = rule(desired=[{"entity_id": "light.porch_a", "state": "on", "data": {"brightness_pct": 30}},
                          {"entity_id": "climate.house", "state": "heat", "data": {"temperature": 21}}],
                 alternate=[{"entity_id": "switch.other", "state": "off"}])
        validate_rules([r])
        self.assertTrue(event_affects(r, "switch.other"))
        custom = {"entity_id": "light.porch_a", "service": "light.turn_on", "satisfied": [{"entity_id": "sensor.verify", "value": "ok"}]}
        r["desired"] = [custom]
        self.assertTrue(event_affects(r, "sensor.verify"))
        for entries in [[{"entity_id": "binary_sensor.night", "state": "on"}],
                        [{**custom, "entity_id": "sensor.verify"}], [custom, custom], [],
                        [{"entity_id": "climate.house", "state": "heat", "data": {"brightness_pct": 30}}]]:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                validate_rules([{**r, "desired": entries}])
        validate_rules([{**r, "desired": [], "alternate": []}], draft=True)
        with self.assertRaises(ValueError):
            validate_rules([{**r, "targets": ["light.porch_a"]}])
        with self.assertRaises(ValueError):
            validate_rules([{**r, "desired": {"state": "on"}}])

    def test_validation_and_multi_targets(self):
        validate_rules([rule()])
        for change in [{"priority": 101}, {"desired": []}, {"desired": [{"entity_id": "light.porch_a", "state": True}]}, {"desired": [{"entity_id": "light.porch_a", "state": "on", "data": {"brightness_pct": 0}}]}, {"unexpected": 1}, {"trigger": {"type": "entity", "entity_id": "x.y", "value": "on", "operator": []}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_rules([rule(**change)])
        with self.assertRaises(ValueError):
            validate_rules([rule(), rule()])

    def test_overnight_boundaries(self):
        r = rule(trigger={"type": "time", "start": "21:00", "end": "01:00"})
        for hour, minute, active in [(20, 59, False), (21, 0, True), (0, 59, True), (1, 0, False)]:
            self.assertEqual(active_action(r, {}, datetime(2026, 10, 12, hour, minute))[0] is not None, active)
        draft = rule(trigger={"type": "time", "start": "00:00", "end": "00:00"})
        validate_rules([draft], draft=True)
        with self.assertRaises(ValueError):
            validate_rules([draft])

    def test_unknown_does_not_invert_and_conditions_gate_both(self):
        r = rule(alternate=[{"entity_id": "light.porch_a", "state": "off"}], conditions=[{"entity_id": "switch.manual", "value": "off"}])
        states = {"binary_sensor.night": state("unavailable"), "switch.manual": state("off")}
        self.assertIsNone(active_action(r, states, datetime.now())[0])
        states["binary_sensor.night"] = state("off")
        self.assertEqual(active_action(r, states, datetime.now())[0], r["alternate"])
        states["switch.manual"] = state("on")
        self.assertIsNone(active_action(r, states, datetime.now())[0])
        r["trigger"]["unknown"] = "match"
        states["switch.manual"] = state("off")
        states["binary_sensor.night"] = state("unknown")
        self.assertEqual(active_action(r, states, datetime.now())[0], r["desired"])

    def test_predicates_numeric_and_tolerance(self):
        states = {"sensor.temp": state("22.1", temperature=22.1)}
        self.assertTrue(predicate({"entity_id": "sensor.temp", "operator": "gt", "value": 20}, states))
        self.assertTrue(predicate({"entity_id": "sensor.temp", "value": 22, "tolerance": .2}, states))
        self.assertTrue(predicate({"entity_id": "sensor.temp", "attribute": "temperature", "value": 22, "tolerance": .2}, states))
        self.assertIsNone(predicate({"entity_id": "sensor.temp", "attribute": "missing", "value": 22}, states))

    def test_light_matches_details_not_transition(self):
        desired = {"state": "on", "data": {"brightness_pct": 50, "transition": 30, "rgb_color": [255, 0, 0]}}
        states = {"light.porch_a": state("on", brightness=127, rgb_color=[253, 0, 0])}
        self.assertEqual(action_commands("light.porch_a", desired, states), [])
        states["light.porch_a"]["attributes"]["brightness"] = 20
        self.assertEqual(action_commands("light.porch_a", desired, states)[0][0], "light.turn_on")
        states["light.porch_a"] = state("unavailable")
        self.assertIsNone(action_commands("light.porch_a", desired, states))

    def test_domain_adapters(self):
        cases = [
            ("cover.shade", state("open", current_position=50), {"state": "open", "data": {"position": 50}}, []),
            ("cover.shade", state("closed", current_position=50), {"state": "open", "data": {"position": 50}}, [("cover.set_cover_position", {"position": 50})]),
            ("climate.house", state("heat", temperature=22), {"state": "heat", "data": {"temperature": 22}}, []),
            ("fan.house", state("on", percentage=25), {"state": "on", "data": {"percentage": 50}}, [("fan.set_percentage", {"percentage": 50})]),
            ("humidifier.house", state("on", humidity=40), {"state": "on", "data": {"humidity": 50}}, [("humidifier.set_humidity", {"humidity": 50})]),
            ("number.house", state("10"), {"data": {"value": 10}}, []),
            ("select.house", state("eco"), {"data": {"option": "eco"}}, []),
            ("lock.house", state("unlocked"), {"state": "locked"}, [("lock.lock", {})]),
        ]
        for entity, current, desired, expected in cases:
            with self.subTest(entity=entity):
                validate_rules([rule(desired=[{"entity_id": entity, **desired}])])
                self.assertEqual(action_commands(entity, desired, {entity: current}), expected)

    def test_custom_requires_satisfied_and_cannot_override_targets(self):
        action = {"service": "light.turn_on", "data": {}, "satisfied": [{"entity_id": "$target", "value": "on"}]}
        validate_rules([rule(desired=[{"entity_id": "light.porch_a", **action}])])
        self.assertEqual(action_commands("light.porch_a", action, {"light.porch_a": state("on")}), [])
        self.assertEqual(len(action_commands("light.porch_a", action, {"light.porch_a": state("off")})), 1)
        for bad in [{"service": "light.turn_on"}, {**action, "data": {"entity_id": "light.other"}}]:
            with self.assertRaises(ValueError):
                validate_rules([rule(desired=[{"entity_id": "light.porch_a", **bad}])])

    def test_reevaluation_options_and_satisfied_dependencies(self):
        r = rule(reevaluate={"source": False, "target": False, "condition": False, "interval": 0})
        self.assertFalse(event_affects(r, "binary_sensor.night"))
        self.assertFalse(event_affects(r, "light.porch_a"))
        r["reevaluate"]["condition"] = True
        r["desired"] = [{"entity_id": "light.porch_a", "service": "light.turn_on", "satisfied": [{"entity_id": "sensor.verify", "value": "ok"}]}]
        self.assertTrue(event_affects(r, "sensor.verify"))
        r["desired"][0]["satisfied"] = [{"entity_id": "$target", "value": "on"}]
        self.assertFalse(event_affects(r, "light.porch_a"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "syncs.yaml"
        self.store = Store(self.path)

    def test_roundtrip_stale_ui_and_manual_edits(self):
        self.store.save([rule()], self.store.revision)
        revision = self.store.revision
        self.path.write_text(yaml.safe_dump([rule(name="Manual edit")]))
        with self.assertRaises(Conflict):
            self.store.save([rule()], revision)
        self.assertTrue(self.store.reload())
        self.assertEqual(self.store.rules[0]["name"], "Manual edit")
        with self.assertRaises(Conflict):
            self.store.save([rule()], revision)

    def test_invalid_file_not_overwritten(self):
        self.path.write_text("[broken")
        self.store.reload()
        self.assertTrue(self.store.error)
        with self.assertRaises(ValueError):
            self.store.save([], self.store.revision)
        self.assertEqual(self.path.read_text(), "[broken")
        self.path.write_text("[]\n")
        self.assertTrue(self.store.reload())
        self.assertIsNone(self.store.error)

    def test_yaml_types_and_recursive_alias_rejected(self):
        for content in ["null", "&r [*r]", "- !!python/object:os.system {}"]:
            with self.assertRaises(ValueError):
                parse(content)

    def test_per_entity_yaml_overrides_and_persistence(self):
        r = rule(desired=[{"entity_id": "light.porch_a"}], alternate=[{"entity_id": "switch.other", "state": "off"}])
        custom = "service: light.turn_on\nsatisfied:\n  - entity_id: $target\n    value: 'on'\n"
        parsed = parse(json.dumps([r]), {"desired": {"light.porch_a": custom}})
        self.store.save(parsed, self.store.revision)
        reloaded = Store(self.path)
        self.assertEqual(reloaded.rules, parsed)
        self.assertEqual(reloaded.rules[0]["desired"][0]["entity_id"], "light.porch_a")
        for override in [custom, {"light.missing": custom}, {"light.porch_a": "entity_id: light.other\nstate: 'on'"}, {"light.porch_a": []}]:
            with self.assertRaises(ValueError):
                parse(json.dumps([r]), {"desired": override})


class FakeHA:
    connected = True
    zone = ZoneInfo("America/Chicago")

    def __init__(self):
        self.states = {"binary_sensor.night": state("on"), "light.porch_a": state("off"), "light.porch_b": state("off")}
        self.calls = []
        self.fail = False

    async def call(self, service, entity, data):
        self.calls.append((service, entity, data))
        if self.fail:
            raise ValueError("Device rejected service")


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def make_engine(self, rules):
        return Engine(SimpleNamespace(rules=rules, error=None), FakeHA())

    async def test_configuration_and_snooze_changes_do_not_trigger_unrelated_rules(self):
        opts = {"source": True, "condition": True, "target": False, "interval": 0}
        engine = self.make_engine([
            rule("toggle", enabled=False, desired=[{"entity_id": "light.porch_a", "state": "on"}], reevaluate=opts),
            rule("passive", desired=[{"entity_id": "light.porch_b", "state": "on"}], reevaluate=opts),
            rule("condition_only", desired=[{"entity_id": "switch.other", "state": "on"}],
                 conditions=[{"entity_id": "switch.gate", "value": "off"}],
                 reevaluate={**opts, "source": False}),
        ])
        engine.ha.states["light.porch_a"] = state("on")
        engine.ha.states["light.porch_b"] = state("on")
        engine.ha.states["switch.other"] = state("on")
        engine.ha.states["switch.gate"] = state("off")
        await engine.reconcile()
        engine.ha.states["light.porch_a"] = state("off")
        engine.ha.states["light.porch_b"] = state("off")
        engine.ha.states["switch.other"] = state("off")
        previous = copy.deepcopy(engine.store.rules)
        engine.store.rules[0]["enabled"] = True
        engine.config_changed(previous)
        await engine.reconcile()
        self.assertEqual(engine.ha.calls, [("light.turn_on", "light.porch_a", {})])
        previous = copy.deepcopy(engine.store.rules)
        engine.store.rules[0]["enabled"] = False
        engine.config_changed(previous)
        await engine.reconcile()
        engine.snooze("toggle", 300)
        await engine.reconcile()
        engine.snooze("toggle", 0)
        await engine.reconcile()
        engine.snooze("toggle", 300)
        engine.snoozes["toggle"] = 0
        await engine.reconcile()
        self.assertEqual(len(engine.ha.calls), 1)
        self.assertIn("waiting for enabled reevaluation", engine.status["passive"]["targets"]["light.porch_b"])
        engine.changed("binary_sensor.night")
        await engine.reconcile()
        self.assertEqual(engine.ha.calls[-1], ("light.turn_on", "light.porch_b", {}))
        self.assertEqual(len(engine.ha.calls), 2)
        engine.changed("switch.gate")
        await engine.reconcile()
        self.assertEqual(engine.ha.calls[-1], ("switch.turn_on", "switch.other", {}))

    async def test_config_changes_do_not_enforce_unchanged_winner_on_shared_target(self):
        opts = {"source": True, "condition": True, "target": False, "interval": 0}
        engine = self.make_engine([
            rule("higher", 80, desired=[{"entity_id": "light.porch_a", "state": "on"}], reevaluate=opts),
            rule("lower", enabled=False, desired=[{"entity_id": "light.porch_a", "state": "off"}], reevaluate=opts),
        ])
        engine.ha.states["light.porch_a"] = state("on")
        await engine.reconcile()
        engine.ha.states["light.porch_a"] = state("off")
        previous = copy.deepcopy(engine.store.rules)
        engine.store.rules[1]["enabled"] = True
        engine.config_changed(previous)
        await engine.reconcile()
        self.assertFalse(engine.ha.calls)
        self.assertIn("Overridden", engine.status["lower"]["targets"]["light.porch_a"])
        previous = engine.store.rules
        engine.store.rules = engine.store.rules[1:]
        engine.ha.states["light.porch_a"] = state("on")
        engine.config_changed(previous)
        await engine.reconcile()
        self.assertFalse(engine.ha.calls)
        engine.changed("binary_sensor.night")
        await engine.reconcile()
        self.assertEqual(engine.ha.calls, [("light.turn_off", "light.porch_a", {})])

    async def test_per_entity_settings_independent_alternate_and_priority(self):
        r = rule(desired=[{"entity_id": "light.porch_a", "state": "on", "data": {"brightness_pct": 30}},
                          {"entity_id": "light.porch_b", "state": "on", "data": {"brightness_pct": 80}}],
                 alternate=[{"entity_id": "switch.other", "state": "off"}])
        engine = self.make_engine([r])
        engine.ha.states["switch.other"] = state("on")
        await engine.reconcile()
        self.assertEqual(engine.ha.calls, [("light.turn_on", "light.porch_a", {"brightness_pct": 30}),
                                         ("light.turn_on", "light.porch_b", {"brightness_pct": 80})])
        engine.ha.states["binary_sensor.night"] = state("off")
        engine.changed("binary_sensor.night")
        await engine.reconcile()
        self.assertEqual(engine.ha.calls[-1], ("switch.turn_off", "switch.other", {}))
        self.assertEqual(set(engine.pending), {"switch.other"})
        engine.store.rules.append(rule("higher", 80, desired=[{"entity_id": "switch.other", "state": "on"}], alternate=[{"entity_id": "switch.other", "state": "on"}]))
        winners, status = engine.winners(datetime.now(engine.ha.zone), time.monotonic())
        self.assertEqual(winners["switch.other"][0]["id"], "higher")
        self.assertIn("Overridden", status["porch"]["targets"]["switch.other"])

    async def test_conflict_only_overrides_one_target_and_ties_stable(self):
        engine = self.make_engine([rule(), rule("special", 80, desired=[{"entity_id": "light.porch_a", "state": "off"}])])
        await engine.reconcile()
        self.assertEqual(engine.ha.calls, [("light.turn_on", "light.porch_b", {})])
        self.assertIn("Overridden", engine.status["porch"]["targets"]["light.porch_a"])
        engine.store.rules[1]["priority"] = 50
        winners, _ = engine.winners(datetime.now(engine.ha.zone), time.monotonic())
        self.assertEqual(winners["light.porch_a"][0]["id"], "porch")

    async def test_cooldown_no_duplicate_and_retry_checks_current_state(self):
        engine = self.make_engine([rule(desired=[{"entity_id": entity, "state": "on", "data": {"transition": 30}} for entity in ("light.porch_a", "light.porch_b")])])
        await engine.reconcile()
        engine.changed("light.porch_a")
        await engine.reconcile()
        self.assertEqual(len(engine.ha.calls), 2)
        self.assertGreater(engine.pending["light.porch_a"][1] - time.monotonic(), 30)
        engine.ha.states["light.porch_a"] = state("on")
        engine.changed("light.porch_a")
        await engine.reconcile()
        self.assertEqual(len(engine.ha.calls), 2)
        self.assertNotIn("light.porch_a", engine.pending)
        self.assertEqual(engine.status["porch"]["targets"]["light.porch_a"], "In sync")

    async def test_snooze_expiry_disable_and_disconnect(self):
        engine = self.make_engine([rule()])
        engine.snooze("porch", 300)
        await engine.reconcile()
        self.assertFalse(engine.ha.calls)
        engine.snoozes["porch"] = 0
        await engine.reconcile()
        self.assertEqual(len(engine.ha.calls), 2)
        engine.ha.connected = False
        engine.changed()
        await engine.reconcile()
        self.assertEqual(len(engine.ha.calls), 2)
        self.assertIn("disconnected", engine.status["porch"]["reason"])

    async def test_failure_backoff_and_condition_recheck(self):
        engine = self.make_engine([rule()]); engine.ha.fail = True
        await engine.reconcile()
        for entity, pending in list(engine.pending.items()):
            engine.pending[entity] = (pending[0], 0, pending[2], pending[3])
        await engine.reconcile()
        self.assertEqual(engine.pending["light.porch_a"][3], 20)
        engine.ha.states["binary_sensor.night"] = state("off")
        engine.changed("binary_sensor.night")
        await engine.reconcile()
        self.assertFalse(engine.pending)
        self.assertEqual(len(engine.ha.calls), 4)

    async def test_all_options_disabled_and_invalid_config_pause(self):
        engine = self.make_engine([rule(reevaluate={"source": False, "target": False, "condition": False, "interval": 0})])
        engine.force = False
        engine.changed("light.porch_a")
        await engine.reconcile()
        self.assertFalse(engine.ha.calls)
        engine.store.error = "Bad YAML"; engine.changed()
        await engine.reconcile()
        self.assertFalse(engine.ha.calls)

    async def test_ingress_allows_only_real_proxy_peer(self):
        app = create_app(SimpleNamespace(), {}, "http://supervisor/core/api", "unused")
        async def handler(request):
            return web.Response(text="ok")
        for peer, allowed in [("172.30.32.2", True), ("172.30.32.3", False), ("127.0.0.1", False)]:
            transport = SimpleNamespace(get_extra_info=lambda name, p=peer: (p, 123) if name == "peername" else None)
            request = make_mocked_request("GET", "/", headers={"X-Forwarded-For": "172.30.32.2"}, transport=transport)
            if allowed:
                self.assertEqual((await app.middlewares[0](request, handler)).status, 200)
            else:
                with self.assertRaises(web.HTTPForbidden):
                    await app.middlewares[0](request, handler)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "syncs.yaml")
        self.states = {"binary_sensor.night": state("on"), "light.porch_a": state("off"), "light.porch_b": state("off")}
        self.calls, self.streams = [], []

        async def websocket(request):
            ws = web.WebSocketResponse(); await ws.prepare(request)
            self.streams.append(ws)
            await ws.send_json({"type": "auth_required"})
            auth = await ws.receive_json(); self.assertEqual(auth["access_token"], "test-token")
            await ws.send_json({"type": "auth_ok"})
            async for message in ws:
                value = message.json()
                result = None
                if value["type"] == "get_states":
                    result = [{"entity_id": e, **s} for e, s in self.states.items()]
                elif value["type"] == "get_config":
                    result = {"time_zone": "America/Chicago"}
                elif value["type"] == "get_services":
                    result = {"light": {"turn_on": {}, "turn_off": {}}}
                await ws.send_json({"type": "result", "id": value["id"], "success": True, "result": result})
            return ws

        async def service(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            body = await request.json(); self.calls.append((request.match_info["service"], body))
            entity = body["entity_id"]
            self.states[entity] = state("on" if request.match_info["service"] == "turn_on" else "off")
            for ws in self.streams:
                if not ws.closed:
                    await ws.send_json({"type": "event", "id": 1, "event": {"data": {"entity_id": entity, "new_state": {"entity_id": entity, **self.states[entity]}}}})
            return web.json_response([])

        fake = web.Application()
        fake.add_routes([web.get("/api/websocket", websocket), web.post("/api/services/light/{service}", service)])
        self.fake = TestServer(fake); await self.fake.start_server(); self.addAsyncCleanup(self.fake.close)
        self.client = TestClient(TestServer(create_app(self.store, {}, str(self.fake.make_url("/api")), "test-token", True)))
        await self.client.start_server(); self.addAsyncCleanup(self.client.close)
        await self.until(lambda: self.client.app[HA].connected)

    async def until(self, fn):
        async with asyncio.timeout(5):
            while not fn():
                await asyncio.sleep(.01)

    async def post(self, endpoint, body):
        return await self.client.post(f"/api/{endpoint}", json=body, headers={"X-State-Sync": "1"})

    async def test_save_reconcile_no_spam_reconnect_and_stale_edit(self):
        response = await self.post("rule", {"yaml": json.dumps([rule()]), "revision": self.store.revision})
        self.assertEqual(response.status, 200)
        await self.until(lambda: len(self.calls) == 2)
        await asyncio.sleep(.1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.client.app[HA].states["light.porch_a"]["state"], "on")
        await self.streams[-1].close()
        await self.until(lambda: len(self.streams) == 2 and self.client.app[HA].connected)
        self.assertEqual(len(self.calls), 2)
        response = await self.post("rule", {"id": "porch", "yaml": json.dumps([rule()]), "revision": "stale"})
        self.assertEqual(response.status, 409)

    async def test_toggle_endpoint_does_not_force_source_only_sync(self):
        opts = {"source": True, "condition": True, "target": False, "interval": 0}
        toggle = rule("toggle", enabled=False, desired=[{"entity_id": "light.porch_a", "state": "on"}], reevaluate=opts)
        passive = rule("passive", desired=[{"entity_id": "light.porch_b", "state": "off"}], reevaluate=opts)
        for r in (toggle, passive):
            response = await self.post("rule", {"yaml": json.dumps([r]), "revision": self.store.revision})
            self.assertEqual(response.status, 200)
        await self.until(lambda: "passive" in self.client.app[ENGINE].status)
        self.client.app[HA].states["light.porch_b"] = state("on")
        toggle["enabled"] = True
        response = await self.post("rule", {"id": "toggle", "yaml": json.dumps([toggle]), "revision": self.store.revision})
        self.assertEqual(response.status, 200)
        await self.until(lambda: len(self.calls) >= 1)
        await asyncio.sleep(.05)
        self.assertEqual([body["entity_id"] for _, body in self.calls], ["light.porch_a"])

    async def test_resync_all_ignores_event_settings_but_preserves_pauses(self):
        opts = {"source": False, "target": False, "condition": False, "interval": 0}
        for ident, entity, enabled in [("a", "light.porch_a", True), ("b", "light.porch_b", True),
                                       ("disabled", "light.disabled", False), ("snoozed", "light.snoozed", True)]:
            r = rule(ident, enabled=enabled, desired=[{"entity_id": entity, "state": "off"}], reevaluate=opts)
            response = await self.post("rule", {"yaml": json.dumps([r]), "revision": self.store.revision})
            self.assertEqual(response.status, 200)
        await self.post("snooze", {"id": "snoozed", "seconds": 300})
        await self.until(lambda: self.client.app[ENGINE].status.get("snoozed", {}).get("reason") == "Snoozed")
        for entity in ("light.porch_a", "light.porch_b", "light.disabled", "light.snoozed"):
            self.client.app[HA].states[entity] = state("on")
        response = await self.client.post("/api/resync", json={})
        self.assertEqual(response.status, 403)
        response = await self.post("resync", {})
        self.assertEqual(response.status, 200)
        await self.until(lambda: len(self.calls) >= 2)
        self.assertEqual({body["entity_id"] for _, body in self.calls}, {"light.porch_a", "light.porch_b"})

    async def test_save_per_entity_actions_and_reject_sensor_target(self):
        r = rule(desired=[{"entity_id": "light.porch_a"}, {"entity_id": "light.porch_b", "state": "off"}],
                 alternate=[{"entity_id": "light.porch_b", "state": "on"}])
        custom = "service: light.turn_on\nsatisfied:\n  - entity_id: $target\n    value: 'on'\n"
        response = await self.post("rule", {"yaml": json.dumps([r]), "actions": {"desired": {"light.porch_a": custom}}, "revision": self.store.revision})
        self.assertEqual(response.status, 200)
        await self.until(lambda: len(self.calls) == 1)
        self.assertEqual(self.calls[0][1]["entity_id"], "light.porch_a")
        revision = self.store.revision
        invalid = {**self.store.rules[0], "desired": [{"entity_id": "binary_sensor.night", "state": "off"}]}
        response = await self.post("rule", {"id": "porch", "yaml": json.dumps([invalid]), "revision": revision})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.store.revision, revision)

    async def test_ui_assets_security_and_custom_yaml_preview(self):
        for path in ["/", "/app.js", "/style.css", "/theme.css", "/api/snapshot", "/api/entities"]:
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
            self.assertIn("nosniff", response.headers["X-Content-Type-Options"])
        response = await self.client.post("/api/rule", json={})
        self.assertEqual(response.status, 403)
        custom = "service: light.turn_on\ndata: {}\nsatisfied:\n  - entity_id: $target\n    value: 'on'\n"
        response = await self.post("preview", {"yaml": json.dumps([rule()]), "actions": {"desired": {"light.porch_a": custom}}})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["rule"]["desired"][0]["service"], "light.turn_on")
        response = await self.post("rule", {"yaml": "- id: bad", "revision": self.store.revision})
        self.assertEqual(response.status, 400)

    async def test_live_snapshots_save_device_confirmation_snooze_and_disconnect(self):
        response = await self.client.get("/api/events")
        self.addCleanup(response.close)
        self.assertEqual(response.headers["Content-Type"], "text/event-stream")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

        async def update_until(predicate):
            async with asyncio.timeout(5):
                while True:
                    line = await response.content.readline()
                    if line.startswith(b"data: "):
                        data = json.loads(line[6:])
                        if predicate(data):
                            return data

        initial = await update_until(lambda data: data["connected"])
        self.assertEqual(initial["rules"], [])
        await self.post("rule", {"yaml": json.dumps([rule()]), "revision": self.store.revision})
        await update_until(lambda data: data["status"].get("porch", {}).get("targets", {}).get("light.porch_a") == "In sync")
        await self.post("snooze", {"id": "porch", "seconds": 300})
        snoozed = await update_until(lambda data: data["status"].get("porch", {}).get("reason") == "Snoozed")
        self.assertGreater(snoozed["status"]["porch"]["snooze_seconds"], 290)
        await self.post("snooze", {"id": "porch", "seconds": 0})
        await update_until(lambda data: data["status"].get("porch", {}).get("snooze_seconds") == 0)
        await self.streams[-1].close()
        disconnected = await update_until(lambda data: not data["connected"])
        self.assertIn("disconnected", disconnected["status"]["porch"]["reason"])

    async def test_incomplete_draft_preview_does_not_weaken_save_validation(self):
        draft = rule(name="", desired=[], trigger={"type": "entity", "entity_id": "", "value": "on"},
                     conditions=[{"entity_id": "", "value": "off"}])
        response = await self.post("preview", {"yaml": json.dumps([draft]), "draft": True})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["rule"], draft)
        response = await self.post("rule", {"yaml": json.dumps([draft]), "draft": True, "revision": self.store.revision})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.store.rules, [])
        for invalid in [rule(name="", priority=101), rule(name="", trigger={"type": "entity", "entity_id": [], "value": "on"})]:
            response = await self.post("preview", {"yaml": json.dumps([invalid]), "draft": True})
            self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
