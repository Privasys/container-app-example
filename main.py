"""Privasys container-app-example.

Demonstrates the configure-then-freeze pattern for container apps:

  1. The app boots in a frozen state. Every endpoint other than
     POST /configure returns 503 with the body
     ``{"error": "app is awaiting initial configuration"}``.
  2. The deployer POSTs ``{"api_key": "..."}`` to /configure. The app
     a. stores the key under /data/api_key (mounted on the per-app
        sealed volume),
     b. computes SHA-256(api_key),
     c. POSTs the hash to the local manager
        ``http://127.0.0.1:9443/api/v1/containers/{name}/attestation-extensions``
        with the Authorization Bearer token injected by the launcher,
        so that the next per-container RA-TLS leaf advertises the
        commitment under OID ``1.3.6.1.4.1.65230.3.5.1``,
     d. POSTs to ``.../config-complete`` to lift the freeze on the
        manager side too.
  3. /protected returns 200 once the app has been configured.
  4. On restart the in-memory ``configured`` flag is reset to False
     so the deployer must re-supply the key before traffic resumes.

The launcher injects two environment variables at start time:
``PRIVASYS_CONTAINER_NAME`` and ``PRIVASYS_CONTAINER_TOKEN``. The
manager middleware enforces (loopback origin + token + name) before
honouring the SDK callbacks, so neither value needs to be guarded
within the app.
"""

import base64
import hashlib
import http.client
import http.server
import json
import os
import threading
from pathlib import Path

# ── Per-app state ────────────────────────────────────────────────────
_CONFIG_LOCK = threading.Lock()
_CONFIGURED = False  # in-memory: re-armed on every container restart
_DATA_DIR = Path("/data")
_KEY_PATH = _DATA_DIR / "api_key"

_MANAGER_HOST = "127.0.0.1"
_MANAGER_PORT = 9443

_NAME = os.environ.get("PRIVASYS_CONTAINER_NAME", "")
_TOKEN = os.environ.get("PRIVASYS_CONTAINER_TOKEN", "")


def _post_to_manager(path: str, body: dict) -> tuple[int, bytes]:
    """POST a JSON body to the local manager and return (status, body)."""
    if not _NAME or not _TOKEN:
        raise RuntimeError(
            "PRIVASYS_CONTAINER_NAME / PRIVASYS_CONTAINER_TOKEN missing; "
            "is this container running on enclave-os-virtual?"
        )
    conn = http.client.HTTPConnection(_MANAGER_HOST, _MANAGER_PORT, timeout=5)
    try:
        conn.request(
            "POST",
            path,
            body=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_TOKEN}",
            },
        )
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _do_configure(api_key: str) -> None:
    """Persist + commit + unfreeze. Raises on any failure."""
    if not api_key:
        raise ValueError("api_key must be non-empty")

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _KEY_PATH.write_text(api_key)
    os.chmod(_KEY_PATH, 0o600)

    digest = hashlib.sha256(api_key.encode("utf-8")).digest()
    status, body = _post_to_manager(
        f"/api/v1/containers/{_NAME}/attestation-extensions",
        {
            "oid": "1.3.6.1.4.1.65230.3.5.1",
            "value_b64": base64.standard_b64encode(digest).decode("ascii"),
        },
    )
    if status >= 300:
        raise RuntimeError(f"manager attestation-extensions: {status} {body!r}")

    status, body = _post_to_manager(
        f"/api/v1/containers/{_NAME}/config-complete",
        {},
    )
    if status >= 300:
        raise RuntimeError(f"manager config-complete: {status} {body!r}")


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_frozen_for(self, path: str) -> bool:
        # Health check is always available so the manager's readiness
        # probe (and operator dashboards) can see the container is up
        # even before it is configured.
        if path == "/health":
            return False
        with _CONFIG_LOCK:
            return not _CONFIGURED

    def do_GET(self) -> None:  # noqa: N802
        if self._is_frozen_for(self.path):
            self._json(503, {"error": "app is awaiting initial configuration"})
            return

        if self.path == "/health":
            self._json(200, {"status": "healthy"})
        elif self.path == "/protected":
            try:
                key = _KEY_PATH.read_text()
            except FileNotFoundError:
                self._json(500, {"error": "api_key file missing"})
                return
            self._json(200, {"status": "ok", "api_key_length": len(key)})
        elif self.path == "/":
            self._json(200, {"status": "ok", "name": _NAME})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/configure":
            if self._is_frozen_for(self.path):
                self._json(503, {"error": "app is awaiting initial configuration"})
                return
            self._json(404, {"error": "not found"})
            return

        # /configure is allowed even while frozen.
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON body"})
            return
        api_key = payload.get("api_key", "")
        if not isinstance(api_key, str) or not api_key:
            self._json(400, {"error": "api_key (string) is required"})
            return

        try:
            _do_configure(api_key)
        except Exception as exc:  # noqa: BLE001 — surface manager error
            self._json(500, {"error": str(exc)})
            return

        global _CONFIGURED
        with _CONFIG_LOCK:
            _CONFIGURED = True
        self._json(200, {"status": "configured"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", 8080), Handler)
    print(f"container-app-example listening on :8080 (name={_NAME or '<unset>'})")
    server.serve_forever()
