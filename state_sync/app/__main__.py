import asyncio
import contextlib
import ipaddress
import json
import logging
import os
from pathlib import Path

import aiohttp
import yaml
from aiohttp import web

from .engine import Engine
from .ha import HomeAssistant
from .model import require
from .store import Conflict, Store, parse

STATIC = Path(__file__).parent / "static"
HA = web.AppKey("ha", HomeAssistant)
ENGINE = web.AppKey("engine", Engine)


def create_app(store, options, api, token, development=False):
    @web.middleware
    async def guard(request, handler):
        try:
            peer = ipaddress.ip_address(request.remote or "0.0.0.0")
            allowed = peer.is_loopback if development else str(peer) == "172.30.32.2"
            if not allowed:
                raise web.HTTPForbidden(text="Use Home Assistant ingress")
            if request.method not in {"GET", "HEAD"}:
                if request.headers.get("X-State-Sync") != "1" or request.content_type != "application/json":
                    raise web.HTTPForbidden(text="Invalid request")
            response = await handler(request)
        except Conflict as exc:
            response = web.json_response({"error": str(exc)}, status=409)
        except (ValueError, yaml.YAMLError, RecursionError) as exc:
            response = web.json_response({"error": str(exc)}, status=400)
        except OSError:
            response = web.json_response({"error": "Unable to read/write sync file; check app logs and permissions"}, status=500)
        return response

    app = web.Application(middlewares=[guard], client_max_size=1024 * 1024)

    async def response_headers(request, response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"

    app.on_response_prepare.append(response_headers)
    subscribers = set()
    last_snapshot = None

    async def close_streams(app):
        for queue in subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)

    app.on_shutdown.append(close_streams)

    def snapshot_data():
        ha, engine = app[HA], app[ENGINE]
        return {
            "rules": store.rules, "revision": store.revision, "status": engine.status,
            "error": store.error or ha.error, "connected": ha.connected,
            "timezone": str(ha.zone),
        }

    def publish():
        nonlocal last_snapshot
        data = json.dumps(snapshot_data())
        if data == last_snapshot:
            return
        last_snapshot = data
        for queue in subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(data)

    async def lifecycle(app):
        async with aiohttp.ClientSession() as session:
            ha = HomeAssistant(session, api, token, lambda entity: engine.changed(entity))
            engine = Engine(store, ha, options.get("retry_seconds", 10), publish)
            app[HA], app[ENGINE] = ha, engine
            tasks = [asyncio.create_task(ha.run()), asyncio.create_task(engine.run())]
            yield
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app.cleanup_ctx.append(lifecycle)

    async def snapshot(request):
        return web.json_response(snapshot_data())

    async def events(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        queue = asyncio.Queue(maxsize=1)
        subscribers.add(queue)
        queue.put_nowait(json.dumps(snapshot_data()))
        try:
            await response.prepare(request)
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    if data is None:
                        break
                    await response.write(f"data: {data}\n\n".encode())
                except TimeoutError:
                    await response.write(b": heartbeat\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            subscribers.discard(queue)
        return response

    async def entities(request):
        return web.json_response(sorted(app[HA].states.values(), key=lambda s: s["entity_id"]))

    async def edit(request):
        body = await request.json()
        require(isinstance(body, dict), "Expected a JSON object")
        text = body.get("yaml")
        require(isinstance(text, str), "Expected YAML text")
        rules = parse(text, body.get("actions"))
        require(len(rules) == 1, "Editor accepts exactly one sync")
        rule = rules[0]
        old_id = body.get("id")
        if old_id is not None:
            require(old_id == rule["id"], "Sync ID cannot be changed")
            require(any(r["id"] == old_id for r in store.rules), "Sync no longer exists")
            updated = [rule if r["id"] == old_id else r for r in store.rules]
        else:
            updated = [*store.rules, rule]
        store.save(updated, body.get("revision"))
        app[ENGINE].changed()
        return web.json_response({"ok": True})

    async def delete(request):
        body = await request.json()
        require(isinstance(body, dict) and isinstance(body.get("id"), str), "Expected sync ID")
        require(any(r["id"] == body["id"] for r in store.rules), "Sync no longer exists")
        store.save([r for r in store.rules if r["id"] != body["id"]], body.get("revision"))
        app[ENGINE].changed()
        return web.json_response({"ok": True})

    async def snooze(request):
        body = await request.json()
        require(isinstance(body, dict), "Expected a JSON object")
        require(type(body.get("seconds")) is int and body["seconds"] in {0, 300, 1800, 3600, 21600, 43200, 86400}, "Invalid snooze duration")
        require(any(r["id"] == body.get("id") for r in store.rules), "Sync no longer exists")
        app[ENGINE].snooze(body["id"], body["seconds"])
        return web.json_response({"ok": True})

    async def rule_yaml(request):
        rule = next((r for r in store.rules if r["id"] == request.match_info["id"]), None)
        if rule is None:
            raise web.HTTPNotFound()
        return web.json_response({"yaml": yaml.safe_dump([rule], sort_keys=False), "revision": store.revision})

    async def preview(request):
        body = await request.json()
        require(isinstance(body, dict) and isinstance(body.get("yaml"), str), "Expected YAML text")
        rules = parse(body["yaml"], body.get("actions"), validate=body.get("validate") is not False, draft=body.get("draft") is True)
        require(len(rules) == 1 and isinstance(rules[0], dict), "Preview accepts one sync")
        return web.json_response({"rule": rules[0], "yaml": yaml.safe_dump(rules, sort_keys=False)})

    async def theme(request):
        import re
        light, dark = options.get("accent_light", "#2563eb"), options.get("accent_dark", "#93b4ff")
        require(all(isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in (light, dark)), "Invalid theme colors")
        return web.Response(text=f":root {{ --accent: {light}; }} @media (prefers-color-scheme: dark) {{ :root {{ --accent: {dark}; }} }}", content_type="text/css")

    async def asset(request):
        name = request.match_info.get("name", "index.html")
        if name not in {"index.html", "style.css", "app.js"}:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / name)

    app.add_routes([
        web.get("/api/snapshot", snapshot), web.get("/api/events", events), web.get("/api/entities", entities),
        web.get("/api/rule/{id}", rule_yaml), web.post("/api/rule", edit),
        web.post("/api/preview", preview),
        web.post("/api/delete", delete), web.post("/api/snooze", snooze),
        web.get("/theme.css", theme), web.get("/", asset), web.get("/{name}", asset),
    ])
    return app


def main():
    development = os.environ.get("STATE_SYNC_DEV") == "1"
    options_path = Path(os.environ.get("STATE_SYNC_OPTIONS", "/data/options.json"))
    options = json.loads(options_path.read_text()) if options_path.exists() else {}
    logging.basicConfig(level=options.get("log_level", "info").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.environ.get("HA_TOKEN") if development else os.environ.get("SUPERVISOR_TOKEN")
    if not token and not development:
        raise SystemExit("SUPERVISOR_TOKEN is required; run through Home Assistant")
    store = Store(os.environ.get("STATE_SYNC_FILE", "/config/syncs.yaml"))
    app = create_app(store, options, os.environ.get("HA_API", "http://supervisor/core/api"), token or "", development)
    web.run_app(app, host="127.0.0.1" if development else "0.0.0.0", port=8099, access_log=None)


if __name__ == "__main__":
    main()
