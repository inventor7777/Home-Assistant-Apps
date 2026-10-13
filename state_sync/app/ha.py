"""Supervisor-authenticated state stream; credentials never reach the UI."""

import asyncio
import logging
from zoneinfo import ZoneInfo

import aiohttp

LOG = logging.getLogger(__name__)


class HomeAssistant:
    def __init__(self, session, api, token, changed):
        self.session, self.api, self.token, self.changed = session, api.rstrip("/"), token, changed
        self.states, self.services = {}, {}
        self.zone = ZoneInfo("UTC")
        self.connected = False
        self.error = "Connecting to Home Assistant"

    def event(self, data):
        entity = data["entity_id"]
        if data.get("new_state") is None:
            self.states.pop(entity, None)
        else:
            self.states[entity] = data["new_state"]
        self.changed(entity)

    async def run(self):
        delay = 1
        ws_url = self.api.removesuffix("/api") + "/websocket" if self.api.endswith("/core/api") else self.api + "/websocket"
        ws_url = ws_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        while True:
            try:
                async with self.session.ws_connect(ws_url, heartbeat=30, timeout=aiohttp.ClientWSTimeout(ws_receive=90)) as ws:
                    async with asyncio.timeout(30):
                        hello = await ws.receive_json()
                        if hello.get("type") != "auth_required":
                            raise ValueError("Unexpected HA authentication response")
                        await ws.send_json({"type": "auth", "access_token": self.token})
                        if (await ws.receive_json()).get("type") != "auth_ok":
                            raise ValueError("Home Assistant authentication failed")
                        for ident, typ in [(1, "subscribe_events"), (2, "get_states"), (3, "get_config"), (4, "get_services")]:
                            msg = {"id": ident, "type": typ}
                            if ident == 1:
                                msg["event_type"] = "state_changed"
                            await ws.send_json(msg)
                        results, buffered = {}, []
                        while len(results) < 4:
                            msg = await ws.receive_json()
                            if msg["type"] == "event":
                                buffered.append(msg["event"]["data"])
                            elif msg["type"] == "result":
                                if not msg["success"]:
                                    raise ValueError("Home Assistant initialization failed")
                                results[msg["id"]] = msg["result"]
                        self.states = {s["entity_id"]: s for s in results[2]}
                        self.zone = ZoneInfo(results[3]["time_zone"])
                        self.services = results[4]
                        for data in buffered:
                            self.event(data)
                        self.connected, self.error, delay = True, None, 1
                        self.changed(None)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            value = msg.json()
                            if value.get("type") == "event":
                                self.event(value["event"]["data"])
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            raise ConnectionError("Home Assistant stream failed")
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError, KeyError) as exc:
                # Do not log upstream response bodies or URLs containing credentials.
                LOG.warning("HA connection lost (%s); retry in %ss", type(exc).__name__, delay)
                self.error = f"HA disconnected ({type(exc).__name__}); reconnecting"
            self.connected = False
            self.error = self.error or "HA disconnected; reconnecting"
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def call(self, service, entity, data):
        domain, name = service.split(".")
        if name not in self.services.get(domain, {}):
            raise ValueError(f"Service {service} is unavailable")
        headers = {"Authorization": f"Bearer {self.token}"}
        async with self.session.post(
            f"{self.api}/services/{domain}/{name}", headers=headers,
            json={**data, "entity_id": entity}, timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if response.status >= 400:
                raise ValueError(f"{service} failed (HTTP {response.status})")
            await response.read()
