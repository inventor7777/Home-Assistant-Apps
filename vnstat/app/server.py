#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


DB_DIR = os.environ.get("VNSTAT_DB_DIR", "/config/vnstat")
CONFIG_FILE = os.environ.get("VNSTAT_CONFIG", "/data/vnstat.conf")
INTERFACE = os.environ.get("VNSTAT_INTERFACE", "")
PORT = int(os.environ.get("APP_PORT", "8099"))
ALLOWED_IPS = {"127.0.0.1", "::1", "172.30.32.2"}
STATIC_DIR = Path(__file__).resolve().parent / "static"


def run_vnstat() -> dict:
    command = ["vnstat", "--json", "--config", CONFIG_FILE]
    if INTERFACE:
        command.extend(["-i", INTERFACE])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


CLI_MODES = {"normal": (), "daily": ("--days",), "monthly": ("--months",), "json": ("--json",)}


def run_vnstat_text(mode: str) -> str:
    command = ["vnstat", "--config", CONFIG_FILE, *CLI_MODES[mode]]
    if INTERFACE:
        command.extend(["-i", INTERFACE])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def run_vnstat_live() -> dict:
    command = ["vnstat", "--config", CONFIG_FILE, "-tr", "2", "--json"]
    if INTERFACE:
        command.extend(["-i", INTERFACE])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


class Handler(BaseHTTPRequestHandler):
    server_version = "vnstat-ha/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if not self._is_allowed():
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return

        parsed = urlparse(self.path)

        if parsed.path == "/api/health":
            self._send_json(
                {
                    "status": "ok",
                    "interface": INTERFACE,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            return

        if parsed.path == "/api/stats":
            try:
                payload = run_vnstat()
            except subprocess.CalledProcessError as err:
                self._send_json(
                    {
                        "status": "error",
                        "message": err.stderr.strip() or "vnstat command failed",
                    },
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._send_json(
                {
                    "status": "ok",
                    "interface": INTERFACE,
                    "data": payload,
                }
            )
            return

        mode = parsed.path.removeprefix("/api/cli/")
        if parsed.path.startswith("/api/cli/") and mode in CLI_MODES:
            try:
                output = run_vnstat_text(mode)
            except subprocess.CalledProcessError as err:
                self._send_json(
                    {
                        "status": "error",
                        "message": err.stderr.strip() or "vnstat command failed",
                    },
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._send_json({"status": "ok", "output": output})
            return

        if parsed.path == "/api/live":
            try:
                payload = run_vnstat_live()
            except subprocess.CalledProcessError as err:
                self._send_json(
                    {
                        "status": "error",
                        "message": err.stderr.strip() or "vnstat live command failed",
                    },
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._send_json({"status": "ok", "data": payload})
            return

        if parsed.path in {"/", "/index.html"}:
            self._serve_static("index.html", "text/html; charset=utf-8")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _is_allowed(self) -> bool:
        client_ip = self.client_address[0]
        return client_ip in ALLOWED_IPS

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _serve_static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIR / filename
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
