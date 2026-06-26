"""Privasys container-app-example.

A minimal container app for the Privasys confidential platform. It boots
**ready** — there is no configuration step — and demonstrates persistent
state on the per-app **sealed volume** at ``/data``, whose encryption key is
reconstructed from the Enclave Vault constellation at boot. The host never
sees the key or the plaintext data.

MCP tools (callable from the developer portal / agents):

  * ``store``  POST ``{"key": "...", "value": "..."}``  — write to /data
  * ``fetch``  POST ``{"key": "..."}``                  — read it back

Plain HTTP endpoints:

  * ``GET /health``   — liveness (the manager's readiness probe hits this)
  * ``GET /version``  — APP_VERSION
  * ``GET /``         — app info

Data under ``/data`` survives restarts and platform/app upgrades (after the
app owner approves the new measurement). For the configure-then-freeze
variant — which boots frozen and refuses traffic until a secret is injected
via ``POST /configure`` — see
https://github.com/Privasys/container-app-example-with-config
"""

import json
import os
import re
import http.server
from pathlib import Path
from urllib.parse import urlparse

APP_VERSION = "1.0.0"

# The platform runs containers on the host network and assigns each one a
# unique port, injected as $PORT. The app MUST listen on it (the manager's
# health probe hits localhost:$PORT/health). Default 8080 for local runs.
_PORT = int(os.environ.get("PORT", "8080"))

# Injected by the launcher; handy for identifying the instance.
_NAME = os.environ.get("PRIVASYS_CONTAINER_NAME", "")

# Per-app encrypted volume. Keys are path components, so constrain them to a
# safe charset (they can never escape the store directory).
_DATA_DIR = Path("/data")
_STORE_DIR = _DATA_DIR / "store"
_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _safe(component: str) -> bool:
    return bool(_SAFE.match(component or ""))


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length else b""

    def _payload(self):
        try:
            return json.loads(self._read_body() or b"{}"), None
        except json.JSONDecodeError:
            return None, "invalid JSON body"

    # ── GET ──────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {"status": "healthy"})
        elif path == "/version":
            self._json(200, {"version": APP_VERSION})
        elif path == "/":
            self._json(200, {"status": "ok", "name": _NAME, "version": APP_VERSION})
        else:
            self._json(404, {"error": "not found"})

    # ── POST ─────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/store":
            self._store()
        elif path == "/fetch":
            self._fetch()
        else:
            self._json(404, {"error": "not found"})

    def _store(self) -> None:
        body, err = self._payload()
        if err:
            self._json(400, {"error": err})
            return
        key, value = body.get("key"), body.get("value")
        if not _safe(key) or not isinstance(value, str):
            self._json(400, {"error": "key (safe string) and value (string) are required"})
            return
        _STORE_DIR.mkdir(parents=True, exist_ok=True)
        path = _STORE_DIR / key
        path.write_text(value)
        os.chmod(path, 0o600)
        self._json(200, {"status": "stored", "key": key, "bytes": len(value.encode("utf-8"))})

    def _fetch(self) -> None:
        body, err = self._payload()
        if err:
            self._json(400, {"error": err})
            return
        key = body.get("key")
        if not _safe(key):
            self._json(400, {"error": "key (safe string) is required"})
            return
        try:
            value = (_STORE_DIR / key).read_text()
        except FileNotFoundError:
            self._json(404, {"error": "key not found"})
            return
        self._json(200, {"key": key, "value": value})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", _PORT), Handler)
    print(f"container-app-example listening on :{_PORT} (name={_NAME or '<unset>'})")
    server.serve_forever()
