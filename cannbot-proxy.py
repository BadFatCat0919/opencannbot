#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CANNBOT Gateway Proxy for Trae IDE
===================================

Background
----------
Trae IDE (https://www.trae.ai) lets users configure a custom OpenAI-compatible
endpoint, but its "API Key" field maps to a single ``Authorization: Bearer
<key>`` header. The CANNBOT gateway
(https://cannbot.hicann.cn/gateway/compatible-mode/v1) requires *two* headers
on every request:

    x-api-vkey: <your Virtual Key, e.g. vk-xxxxxx>
    Authorization: Bearer <short-lived JWT>

The JWT is obtained by exchanging a Virtual Key (VK) at::

    POST https://cannbot.hicann.cn/cannbot/api/auth/authenticate
    Headers: x-api-vkey: <vk>, Content-Type: application/json
    Body:    {"type": "cli", "mac": "<host-mac>"}

This small proxy bridges that gap: it accepts a request from Trae exactly as
Trae would send it, then injects the missing ``x-api-vkey`` header and rewrites
the bearer token to a fresh JWT (refreshing the cached JWT transparently when
it is about to expire).

Features
--------
* Zero third-party dependencies — standard library only.
* VK -> JWT exchange with in-process caching (refresh 60s before expiry).
* Three "auth modes" auto-detected from Trae's ``Authorization`` header:
  - Trae sends ``Bearer vk-xxxx``  -> proxy exchanges VK to JWT.
  - Trae sends ``Bearer <jwt>``    -> proxy keeps JWT, uses config VK for
    ``x-api-vkey`` (handy for testing with a known-good JWT).
  - Trae sends nothing             -> proxy falls back to ``CANNBOT_VK`` env var.
* Streaming-friendly (the proxy is a simple HTTP pass-through; it does not
  buffer SSE chunks, so OpenAI-style streaming responses work as-is).
* Health check endpoint at ``GET /_health`` for local monitoring.
* Honours ``CANNBOT_VK``, ``CANNBOT_PROXY_PORT``, ``CANNBOT_PROXY_HOST`` and
  ``CANNBOT_LOG_LEVEL`` environment variables.

Requirements
------------
* Python 3.8+ (uses ``urllib.request``, ``http.server``, ``dataclasses``).
* Network access to ``https://cannbot.hicann.cn``.

Install
-------
The companion installer ``install-cannbot-trae.sh`` does everything for you:
it downloads this script to ``~/.cannbot/proxy/cannbot-proxy.py`` and writes
a ``com.cannbot.proxy.plist`` for macOS ``launchd`` (or a systemd user unit
on Linux) so the proxy starts on login.

Manual install::

    mkdir -p ~/.cannbot/proxy
    curl -fsSL https://raw.githubusercontent.com/BadFatCat0919/opencannbot/main/cannbot-proxy.py \\
        -o ~/.cannbot/proxy/cannbot-proxy.py
    chmod +x ~/.cannbot/proxy/cannbot-proxy.py

Run interactively (foreground, Ctrl-C to stop)::

    export CANNBOT_VK="vk-xxxxxxxxxxxxxxxxxxxx"
    python3 ~/.cannbot/proxy/cannbot-proxy.py

Run as a background daemon (preferred)::

    ~/.cannbot/proxy/cannbot-proxy.py --daemon \\
        --vk "vk-xxxxxxxxxxxxxxxxxxxx" \\
        --port 8765 \\
        --log ~/.cannbot/proxy/proxy.log

Configure Trae
--------------
1. Open Trae IDE.
2. Go to Settings -> AI -> Model Provider -> "Add Provider" (custom).
3. Set **API Base URL** to ``http://127.0.0.1:8765/v1``.
4. Set **API Key** to your Virtual Key (``vk-xxxxxx``).  The proxy will
   exchange it for a JWT on first request and cache the result.
5. (Optional) Set **Model** to one of the CANNBOT model IDs, e.g.
   ``glm-5.1`` or ``qwen3.7-max``.  Trae discovers models via the
   ``/v1/models`` endpoint, which this proxy also forwards.

Verify
------
After starting the proxy and configuring Trae::

    curl -sS http://127.0.0.1:8765/_health
    # -> {"status": "ok", "vk": "vk-xxxx", "jwt_expires_in": 3540}

    curl -sS http://127.0.0.1:8765/v1/models \\
        -H "Authorization: Bearer vk-xxxx"
    # -> upstream model list, JSON

Configuration
-------------
Environment variables (all optional except ``CANNBOT_VK``):

``CANNBOT_VK``
    Your Virtual Key, e.g. ``vk-xxxxxxxxxxxxxxxxxxxx``.  If unset, the VK
    *must* be supplied per-request by Trae (or via ``--vk`` / ``~/.cannbot/vk``).

``CANNBOT_PROXY_PORT``
    TCP port to listen on (default ``8765``).

``CANNBOT_PROXY_HOST``
    Bind address (default ``127.0.0.1``; set to ``0.0.0.0`` only on trusted
    networks — the proxy has no built-in auth beyond the upstream VK).

``CANNBOT_LOG_LEVEL``
    One of ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` (default ``INFO``).

CLI flags override env vars:

``--vk VK``           same as ``CANNBOT_VK``
``--port PORT``       same as ``CANNBOT_PROXY_PORT``
``--host HOST``       same as ``CANNBOT_PROXY_HOST``
``--log-level LVL``   same as ``CANNBOT_LOG_LEVEL``
``--log FILE``        also tee log lines to FILE (used by the daemon mode)
``--daemon``          fork into background and write PID to
                      ``~/.cannbot/proxy/proxy.pid`` (POSIX only)

Security notes
--------------
* The proxy is **local-only by default** (``127.0.0.1``).  Do not expose it
  to the public internet — there is no auth and your VK/JWT are sensitive.
* Logs are written to stdout (and optionally a file).  JWTs are logged in
  truncated form (first 20 chars) only at ``DEBUG`` level.
* The proxy will refuse to start if the configured VK is missing and no VK
  is supplied per-request.

License
-------
MIT — same as the parent ``opencannbot`` project.
"""

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Defaults (overridable via env / CLI) ────────────────────────────────
GATEWAY_URL = "https://cannbot.hicann.cn/gateway/compatible-mode/v1"
AUTH_URL = "https://cannbot.hicann.cn/cannbot/api/auth/authenticate"
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
DEFAULT_LOG_LEVEL = "INFO"

# ── Logging setup ───────────────────────────────────────────────────────
log = logging.getLogger("cannbot-proxy")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ── JWT cache (process-wide, thread-safe) ───────────────────────────────
@dataclass
class JwtCache:
    access: Optional[str] = None
    expires_at: float = 0.0  # unix seconds
    lock: threading.Lock = threading.Lock()

    def is_valid(self) -> bool:
        return bool(self.access) and self.expires_at > time.time() + 60

    def get(self) -> Optional[str]:
        if self.is_valid():
            return self.access
        return None

    def put(self, access: str, expires_in: int) -> None:
        # Clamp to a sane range; default to 1h if upstream gives garbage.
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            expires_in = 3600
        self.access = access
        self.expires_at = time.time() + expires_in - 60  # refresh 60s early


_jwt = JwtCache()


# ── Helpers ─────────────────────────────────────────────────────────────
def get_mac() -> str:
    """Return a non-zero MAC address from ``networkInterfaces`` if possible.

    Falls back to the all-zero address used by the upstream ``cli`` type
    auth (the gateway treats it the same as a real MAC for the ``cli``
    auth type, so this is safe).
    """
    try:
        import uuid
        # uuid.getnode() is the most portable: it parses /sys/class/net on
        # Linux, uses IORegistry on macOS, and falls back to a random
        # 48-bit value if all else fails.
        mac_int = uuid.getnode()
        if (mac_int >> 40) % 2 == 0:  # least-significant bit of first octet
            return ":".join(f"{(mac_int >> i) & 0xff:02x}" for i in (40, 32, 24, 16, 8, 0))
    except Exception as e:  # pragma: no cover
        log.debug("get_mac() failed: %s", e)
    return "00:00:00:00:00:00"


def exchange_vk_for_jwt(vk: str) -> Optional[str]:
    """Exchange a Virtual Key for a JWT access token (with caching)."""
    if not vk:
        log.error("Cannot exchange empty VK")
        return None

    with _jwt.lock:
        cached = _jwt.get()
        if cached:
            log.debug("Using cached JWT (expires in %ds)", int(_jwt.expires_at - time.time()))
            return cached

        log.info("Exchanging VK for JWT...")
        body = json.dumps({"type": "cli", "mac": get_mac()}).encode("utf-8")
        req = Request(AUTH_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("x-api-vkey", vk)
        try:
            with urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            access = payload.get("accessToken") or payload.get("access_token")
            expires_in = payload.get("expiresIn") or payload.get("expires_in") or 3600
            if not access:
                log.error("Auth response missing accessToken: %s", payload)
                return None
            _jwt.put(access, int(expires_in))
            log.info("JWT obtained, expires in %ds", int(expires_in))
            return access
        except HTTPError as e:
            log.error("VK->JWT exchange HTTP %d: %s", e.code, e.read().decode("utf-8", "replace"))
            return None
        except URLError as e:
            log.error("VK->JWT exchange network error: %s", e.reason)
            return None
        except Exception as e:  # pragma: no cover
            log.error("VK->JWT exchange failed: %s", e)
            return None


def is_vk(key: str) -> bool:
    """Return True if ``key`` looks like a Virtual Key (``vk-...``)."""
    return bool(key) and key.startswith("vk-")


# ── HTTP server ─────────────────────────────────────────────────────────
class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP proxy that injects the CANNBOT auth headers Trae cannot send."""

    server_version = "CANNBOTProxy/1.0"

    # Silence the default BaseHTTPRequestHandler "127.0.0.1 - - [...]" access
    # log; we keep our own structured logger so the user can dial verbosity.
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - shadowing builtin is intentional
        log.debug(format, *args)

    # ── dispatch ────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        self._proxy_request()

    def do_POST(self) -> None:
        self._proxy_request()

    def do_PUT(self) -> None:
        self._proxy_request()

    def do_DELETE(self) -> None:
        self._proxy_request()

    def do_PATCH(self) -> None:
        self._proxy_request()

    # ── core ────────────────────────────────────────────────────────────
    def _proxy_request(self) -> None:
        # 1. Special-case the health endpoint so monitoring tools can probe
        #    the proxy without firing a real upstream request.
        if self.path == "/_health":
            self._handle_health()
            return

        # 2. Read the request body once.
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length > 0 else None

        # 3. Figure out the auth material.
        auth = self.headers.get("Authorization", "")
        provided = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
        cfg_vk = self.server.config_vk  # type: ignore[attr-defined]

        if provided and is_vk(provided):
            vk, jwt = provided, exchange_vk_for_jwt(provided)
        elif provided:
            jwt, vk = provided, cfg_vk
        else:
            vk, jwt = cfg_vk, exchange_vk_for_jwt(cfg_vk)

        if not jwt:
            self._send_json(401, {"error": "Failed to obtain JWT from VK. "
                                          "Check CANNBOT_VK and network connectivity."})
            return
        if not vk:
            self._send_json(500, {"error": "No VK configured. Set CANNBOT_VK or "
                                          "pass vk-xxx as Authorization header."})
            return

        # 4. Rewrite the path: Trae sends ``/v1/chat/completions`` but the
        #    gateway URL already includes ``/v1``, so strip the prefix.
        path = self.path
        if path.startswith("/v1"):
            path = path[3:] or "/"
        if not path.startswith("/"):
            path = "/" + path
        upstream = GATEWAY_URL + path

        # 5. Build the upstream request.
        req = Request(upstream, data=body, method=self.command)
        if body is not None:
            ct = self.headers.get("Content-Type", "application/json")
            req.add_header("Content-Type", ct)
        req.add_header("x-api-vkey", vk)
        req.add_header("Authorization", f"Bearer {jwt}")
        # Pass through Accept to keep SSE streaming working.
        accept = self.headers.get("Accept")
        if accept:
            req.add_header("Accept", accept)

        # 6. Forward.
        try:
            with urlopen(req, timeout=300) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() in ("transfer-encoding", "connection", "content-length"):
                        continue
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except HTTPError as e:
            err = e.read() if hasattr(e, "read") else b""
            log.warning("upstream HTTP %d on %s %s", e.code, self.command, self.path)
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
        except Exception as e:  # pragma: no cover
            log.exception("proxy error")
            self._send_json(502, {"error": f"Proxy error: {e}"})

    # ── helpers ─────────────────────────────────────────────────────────
    def _handle_health(self) -> None:
        cfg_vk = self.server.config_vk  # type: ignore[attr-defined]
        with _jwt.lock:
            jwt_present = bool(_jwt.access)
            expires_in = int(_jwt.expires_at - time.time()) if jwt_present else 0
        body = {
            "status": "ok",
            "vk_configured": bool(cfg_vk),
            "vk_preview": (cfg_vk[:8] + "...") if cfg_vk else None,
            "jwt_cached": jwt_present,
            "jwt_expires_in": expires_in,
            "gateway": GATEWAY_URL,
        }
        self._send_json(200, body)

    def _send_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _ThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that carries the configured VK on every request."""

    def __init__(self, addr: Tuple[str, int], handler, config_vk: str):
        super().__init__(addr, handler)
        self.config_vk = config_vk


# ── CLI / entry point ───────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cannbot-proxy",
        description="Local HTTP proxy that injects CANNBOT auth headers for Trae IDE.",
    )
    p.add_argument("--vk", help="CANNBOT Virtual Key (vk-xxxx). "
                                "Overrides $CANNBOT_VK.")
    p.add_argument("--port", type=int, help="Listen port (default 8765).")
    p.add_argument("--host", help="Bind address (default 127.0.0.1).")
    p.add_argument("--log-level", help="DEBUG/INFO/WARNING/ERROR.")
    p.add_argument("--log", help="Also write logs to this file.")
    p.add_argument("--daemon", action="store_true",
                   help="Fork into background (POSIX only).")
    return p.parse_args()


def _resolve_config(args: argparse.Namespace) -> Tuple[str, str, int, str]:
    vk = args.vk or os.environ.get("CANNBOT_VK") or ""
    if not vk:
        # Allow a fallback file so the daemon-mode unit can ship without env
        # variables baked in.
        fallback = os.path.expanduser("~/.cannbot/vk")
        if os.path.isfile(fallback):
            with open(fallback, "r", encoding="utf-8") as f:
                vk = f.read().strip()
    if not vk or vk == "你的cannbot apikey":
        sys.stderr.write(
            "ERROR: No Virtual Key configured.\n"
            "  Set --vk vk-xxxx, or $CANNBOT_VK, or write to ~/.cannbot/vk.\n"
        )
        sys.exit(2)
    host = args.host or os.environ.get("CANNBOT_PROXY_HOST", DEFAULT_HOST)
    port = args.port or int(os.environ.get("CANNBOT_PROXY_PORT", DEFAULT_PORT))
    log_level = (args.log_level
                 or os.environ.get("CANNBOT_LOG_LEVEL", DEFAULT_LOG_LEVEL))
    return vk, host, port, log_level


def _daemonize(log_file: Optional[str]) -> None:
    """Classic double-fork detach (POSIX)."""
    if os.name != "posix":
        sys.stderr.write("Daemon mode is POSIX only; run without --daemon.\n")
        sys.exit(1)
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = open(os.devnull, "rb")
    os.dup2(devnull.fileno(), 0)
    out = open(log_file, "ab", buffering=0) if log_file else open(os.devnull, "ab")
    os.dup2(out.fileno(), 1)
    os.dup2(out.fileno(), 2)


def main() -> None:
    args = _parse_args()
    vk, host, port, log_level = _resolve_config(args)
    _setup_logging(log_level)

    if args.log:
        try:
            fh = logging.FileHandler(args.log, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            log.addHandler(fh)
        except OSError as e:
            log.warning("Could not open log file %s: %s", args.log, e)

    if args.daemon:
        _daemonize(args.log)
        pid_path = os.path.expanduser("~/.cannbot/proxy/proxy.pid")
        os.makedirs(os.path.dirname(pid_path), exist_ok=True)
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))

    # Validate VK shape early so the user gets a clear error before binding.
    if not is_vk(vk):
        log.warning("VK does not start with 'vk-' (got %r). "
                    "If this is a JWT, the proxy will use it as the bearer "
                    "token and fall back to the configured VK for x-api-vkey.",
                    vk[:8] + "...")

    # Best-effort: pre-warm the JWT so Trae's first call is fast.
    exchange_vk_for_jwt(vk)

    server = _ThreadingHTTPServer((host, port), ProxyHandler, config_vk=vk)

    def _graceful_shutdown(signum, _frame):  # pragma: no cover
        log.info("Caught signal %d, shutting down", signum)
        # ThreadingHTTPServer.shutdown() is not thread-safe; do it from another thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _graceful_shutdown)
        except (ValueError, OSError):
            pass  # not in main thread (daemon mode)

    log.info("CANNBOT proxy listening on http://%s:%d", host, port)
    log.info("  Gateway : %s", GATEWAY_URL)
    log.info("  VK      : %s", vk[:8] + "..." if len(vk) > 8 else vk)
    log.info("Configure Trae -> API Base URL: http://%s:%d/v1", host, port)
    log.info("                    API Key    : your VK (e.g. vk-xxxx)")

    try:
        server.serve_forever()
    finally:
        server.server_close()
        log.info("Proxy stopped")


if __name__ == "__main__":
    main()
