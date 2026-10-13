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
from app.__main__ import create_app, HA
from app.engine import Engine
from app.ha import HomeAssistant
from app.model import action_commands, active_action, event_affects, predicate, validate_rules
from app.store import Conflict, Store, parse


def state(value, **attrs):
    return {"state": value, "attributes": attrs}


def rule(ident="porch", priority=50, **overrides):
    return {"id": ident, "name": ident, "priority": priority,
            "trigger": {"type": "entity", "entity_id": "binary_sensor.night", "value": "on"},
            "targets": ["light.porch_a", "light.porch_b"], "desired": {"state": "on"}, **overrides}


class ModelTests(unittest.TestCase):
    def test_validation_and_multi_targets(self):
        validate_rules([rule()])
        for change in [{"priority": 101}, {"targets": []}, {"desired": {"state": True}}, {"desired": {"state": "on", "data": {"brightness_pct": 0}}}, {"unexpected": 1}, {"trigger": {"type": "entity", "entity_id": "x.y", "value": "on", "operator": []}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_rules([rule(**change)])
        with self.assertRaises(ValueError):
            validate_rules([rule(), rule()])

    def test_overnight_boundaries(self):
        r = rule(trigger={"type": "time", "start": "21:00", "end": "01:00"})
        for hour, minute, active in [(20, 59, False), (21, 0, True), (0, 59, True), (1, 0, False)]:
            self.assertEqual(active_action(r, {}, datetime(2026, 10, 12, hour, minute))[0] is not None, active)

    def test_unknown_does_not_invert_and_conditions_gate_both(self):
        r = rule(alternate={"state": "off"}, conditions=[{"entity_id": "switch.manual", "value": "off"}])
        states = {"binary_sensor.night": state("unavailable"), "switch.manual": state("off")}
        self.assertIsNone(active_action(r, states, datetime.now())[0])
        states["binary_sensor.night"] = state("off")
        self.assertEqual(active_action(r, states, datetime.now())[0], {"state": "off"})
        states["switch.manual"] = state("on")
        self.assertIsNone(active_action(r, states, datetime.now())[0])
        r["trigger"]["unknown"] = "match"
        states["switch.manual"] = state("off")
        states["binary_sensor.night"] = state("unknown")
        self.assertEqual(active_action(r, states, datetime.now())[0], {"state": "on"})

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
                validate_rules([rule(targets=[entity], desired=desired)])
                self.assertEqual(action_commands(entity, desired, {entity: current}), expected)

    def test_custom_requires_satisfied_and_cannot_override_targets(self):
        action = {"service": "light.turn_on", "data": {}, "satisfied": [{"entity_id": "$target", "value": "on"}]}
        validate_rules([rule(desired=action)])
        self.assertEqual(action_commands("light.porch_a", action, {"light.porch_a": state("on")}), [])
        self.assertEqual(len(action_commands("light.porch_a", action, {"light.porch_a": state("off")})), 1)
        for bad in [{"service": "light.turn_on"}, {**action, "data": {"entity_id": "light.other"}}]:
            with self.assertRaises(ValueError):
                validate_rules([rule(desired=bad)])

    def test_reevaluation_options_and_satisfied_dependencies(self):
        r = rule(reevaluate={"source": False, "target": False, "condition": False, "interval": 0})
        self.assertFalse(event_affects(r, "binary_sensor.night"))
        self.assertFalse(event_affects(r, "light.porch_a"))
        r["reevaluate"]["condition"] = True
        r["desired"] = {"service": "light.turn_on", "satisfied": [{"entity_id": "sensor.verify", "value": "ok"}]}
        self.assertTrue(event_affects(r, "sensor.verify"))
        r["desired"]["satisfied"] = [{"entity_id": "$target", "value": "on"}]
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

    async def test_conflict_only_overrides_one_target_and_ties_stable(self):
        engine = self.make_engine([rule(), rule("special", 80, targets=["light.porch_a"], desired={"state": "off"})])
        await engine.reconcile()
        self.assertEqual(engine.ha.calls, [("light.turn_on", "light.porch_b", {})])
        self.assertIn("Overridden", engine.status["porch"]["targets"]["light.porch_a"])
        engine.store.rules[1]["priority"] = 50
        winners, _ = engine.winners(datetime.now(engine.ha.zone), time.monotonic())
        self.assertEqual(winners["light.porch_a"][0]["id"], "porch")

    async def test_cooldown_no_duplicate_and_retry_checks_current_state(self):
        engine = self.make_engine([rule(desired={"state": "on", "data": {"transition": 30}})])
        await engine.reconcile()
        engine.changed("light.porch_a")
        await engine.reconcile()
        self.assertEqual(len(engine.ha.calls), 2)
        self.assertGreater(engine.pending["light.porch_a"][1] - time.monotonic(), 30)
        engine.ha.states["light.porch_a"] = state("on")
        prev = engine.pending["light.porch_a"]
        engine.pending["light.porch_a"] = (prev[0], 0, prev[2], prev[3])
        await engine.reconcile()
        self.assertEqual(len(engine.ha.calls), 2)
        self.assertNotIn("light.porch_a", engine.pending)

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

    async def test_ui_assets_security_and_custom_yaml_preview(self):
        for path in ["/", "/app.js", "/style.css", "/theme.css", "/api/snapshot", "/api/entities"]:
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
            self.assertIn("nosniff", response.headers["X-Content-Type-Options"])
        response = await self.client.post("/api/rule", json={})
        self.assertEqual(response.status, 403)
        custom = "service: light.turn_on\ndata: {}\nsatisfied:\n  - entity_id: $target\n    value: 'on'\n"
        response = await self.post("preview", {"yaml": json.dumps([rule()]), "actions": {"desired": custom}})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["rule"]["desired"]["service"], "light.turn_on")
        response = await self.post("rule", {"yaml": "- id: bad", "revision": self.store.revision})
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
