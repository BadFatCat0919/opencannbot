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

This proxy bridges that gap: it accepts a request from Trae exactly as Trae
would send it, then injects the missing ``x-api-vkey`` header and rewrites
the bearer token to a fresh JWT (refreshing the cached JWT transparently
when it is about to expire).

Key features
------------
* Zero third-party dependencies — standard library only.
* VK -> JWT exchange with thread-safe in-process caching (refresh 60s
  before expiry).
* **Keepalive streaming**: long-running AI responses are streamed chunk by
  chunk; each successful read resets the idle timer, so a single socket
  timeout does NOT abort the request. Only ``KEEPALIVE_IDLE_TIMEOUT`` seconds
  of total silence triggers a real timeout.
* Transient-connection retry (up to 2 retries with backoff on
  ``ConnectionResetError`` / ``ConnectionAbortedError``).
* Health check endpoint at ``GET /_health``.
* Three auth modes auto-detected from Trae's ``Authorization`` header.
* Graceful shutdown on SIGINT / SIGTERM.
* Local-only by default (``127.0.0.1``).

Usage
-----
Run interactively::

    export CANNBOT_VK="vk-xxxxxxxxxxxxxxxxxxxx"
    python3 cannbot-proxy.py

Run as a daemon::

    python3 cannbot-proxy.py --daemon \\
        --vk "vk-xxxxxxxxxxxxxxxxxxxx" \\
        --port 8765 \\
        --log /tmp/cannbot_proxy.log

Then in Trae, set:
  * API Base URL -> http://127.0.0.1:8765/v1
  * API Key      -> your VK (e.g. vk-xxxxx)

Configuration
-------------
Environment variables (all optional except ``CANNBOT_VK``):

``CANNBOT_VK``              Your Virtual Key.
``CANNBOT_PROXY_PORT``      Listen port (default 8765).
``CANNBOT_PROXY_HOST``      Bind address (default 127.0.0.1).
``CANNBOT_KEEPALIVE_IDLE``  Max idle seconds before timeout (default 300).
``CANNBOT_SOCKET_TIMEOUT``  Per-read socket timeout (default 30).
``CANNBOT_LOG_LEVEL``       DEBUG / INFO / WARNING / ERROR (default INFO).

CLI flags override env vars: ``--vk``, ``--port``, ``--host``,
``--log-level``, ``--log``, ``--daemon``.
"""

import argparse
import http.client
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ── Defaults ───────────────────────────────────────────────────────────
GATEWAY_URL = "https://cannbot.hicann.cn/gateway/compatible-mode/v1"
AUTH_URL = "https://cannbot.hicann.cn/cannbot/api/auth/authenticate"
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_KEEPALIVE_IDLE = 300
DEFAULT_SOCKET_TIMEOUT = 30

# ── Logging ─────────────────────────────────────────────────────────────
log = logging.getLogger("cannbot-proxy")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ── JWT cache (process-wide, thread-safe) ───────────────────────────────
_cached_jwt: Optional[str] = None
_cached_jwt_exp: float = 0.0
_jwt_lock = threading.Lock()


def _jwt_is_valid() -> bool:
    return bool(_cached_jwt) and _cached_jwt_exp > time.time() + 60


def exchange_vk_for_jwt(vk: str) -> Optional[str]:
    """Exchange a Virtual Key for a JWT access token (with caching)."""
    global _cached_jwt, _cached_jwt_exp

    if not vk:
        log.error("Cannot exchange empty VK")
        return None

    with _jwt_lock:
        if _jwt_is_valid():
            log.debug("Using cached JWT (expires in %ds)",
                      int(_cached_jwt_exp - time.time()))
            return _cached_jwt

        log.info("Exchanging VK for JWT...")
        body = json.dumps({"type": "cli", "mac": get_mac()}).encode("utf-8")
        req = Request(AUTH_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("x-api-vkey", vk)
        try:
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            access = result.get("accessToken") or result.get("access_token")
            expires_in = result.get("expiresIn") or result.get("expires_in") or 3600
            if not access:
                log.error("Auth response missing accessToken: %s", result)
                return None
            _cached_jwt = access
            _cached_jwt_exp = time.time() + int(expires_in) - 60
            log.info("JWT obtained, expires in %ds", int(expires_in))
            return access
        except HTTPError as e:
            log.error("VK->JWT exchange HTTP %d: %s",
                      e.code, e.read().decode("utf-8", "replace"))
            return None
        except URLError as e:
            log.error("VK->JWT exchange network error: %s", e.reason)
            return None
        except Exception as e:  # pragma: no cover
            log.error("VK->JWT exchange failed: %s", e)
            return None


def get_mac() -> str:
    """Return a non-zero MAC address if possible, else all-zeros placeholder.

    Uses ``uuid.getnode()`` which is portable across macOS / Linux / Windows
    and falls back to a random 48-bit value if no real MAC is found.
    """
    try:
        mac_int = uuid.getnode()
        if (mac_int >> 40) % 2 == 0:  # not a random addr
            return ":".join(
                f"{(mac_int >> i) & 0xff:02x}" for i in (40, 32, 24, 16, 8, 0)
            )
    except Exception as e:
        log.debug("get_mac() failed: %s", e)
    return "00:00:00:00:00:00"


def is_vk(key: Optional[str]) -> bool:
    """Return True if *key* looks like a Virtual Key (``vk-...``)."""
    return bool(key) and key.startswith("vk-")


# ── HTTP handler ─────────────────────────────────────────────────────────
class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP proxy that injects CANNBOT auth headers with keepalive streaming."""

    server_version = "CANNBOTProxy/1.0"

    # --- dispatch ----------------------------------------------------------
    def do_GET(self):
        self._proxy_request()

    def do_POST(self):
        self._proxy_request()

    def do_PUT(self):
        self._proxy_request()

    def do_DELETE(self):
        self._proxy_request()

    def do_PATCH(self):
        self._proxy_request()

    # --- logging -----------------------------------------------------------
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    # --- helpers -----------------------------------------------------------
    def _send_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _build_headers(self, content_type: str, vk: str, jwt: str) -> dict:
        """Build upstream request headers."""
        headers = {
            "x-api-vkey": vk,
            "Authorization": f"Bearer {jwt}",
            "Connection": "close",
            "User-Agent": "CANNBOT-Proxy/1.0",
            "Accept-Encoding": "identity",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _open_upstream(self, url: str) -> http.client.HTTPConnection:
        """Open connection to upstream with initial timeout."""
        parsed = urlparse(url)
        timeout = min(self.server.socket_timeout, self.server.keepalive_idle)
        if parsed.scheme == "https":
            return http.client.HTTPSConnection(
                parsed.hostname, parsed.port or 443, timeout=timeout
            )
        return http.client.HTTPConnection(
            parsed.hostname, parsed.port or 80, timeout=timeout
        )

    def _read_with_keepalive(self, conn, resp):
        """Stream response body with keepalive mechanism.

        - Each successful read resets the idle timer.
        - A single socket timeout does NOT abort; we check the keepalive
          window and retry.
        - Only abort if no data for ``keepalive_idle`` seconds total.
        """
        keepalive_idle = self.server.keepalive_idle
        socket_timeout = self.server.socket_timeout
        last_data_time = time.time()

        while True:
            elapsed = time.time() - last_data_time
            if elapsed > keepalive_idle:
                raise TimeoutError(
                    f"Keepalive timeout: no data for {keepalive_idle}s"
                )

            remaining = keepalive_idle - elapsed
            op_timeout = min(socket_timeout, remaining)
            if conn.sock:
                conn.sock.settimeout(op_timeout)

            try:
                chunk = resp.read1(8192)
            except socket.timeout:
                elapsed_now = time.time() - last_data_time
                log.debug(
                    "Socket timeout, retrying read (%.0fs/%ds idle)",
                    elapsed_now, keepalive_idle,
                )
                continue
            except (ConnectionResetError, BrokenPipeError):
                raise

            if not chunk:
                break

            last_data_time = time.time()
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except BrokenPipeError:
                log.warning("Client disconnected mid-stream")
                return

    # --- core --------------------------------------------------------------
    def _proxy_request(self) -> None:
        # 1. Health check
        if self.path == "/_health":
            self._handle_health()
            return

        # 2. Read request body
        content_length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(content_length) if content_length > 0 else None

        # 3. Extract auth key from Authorization header
        auth_header = self.headers.get("Authorization", "")
        provided_key = (
            auth_header[len("Bearer "):].strip()
            if auth_header.startswith("Bearer ")
            else ""
        )

        log.debug("Incoming path=%s provided_key=%s",
                  self.path, (provided_key[:12] + "...") if provided_key else "(none)")

        # 4. Determine VK and JWT
        cfg_vk = self.server.config_vk
        vk = cfg_vk
        jwt = None

        if provided_key and is_vk(provided_key):
            vk = provided_key
            jwt = exchange_vk_for_jwt(vk)
        elif provided_key:
            jwt = provided_key
        else:
            jwt = exchange_vk_for_jwt(vk)

        if not jwt:
            self._send_json(401, {
                "error": "Failed to obtain JWT from VK. "
                         "Check CANNBOT_VK and network connectivity."
            })
            return

        # 5. Rewrite path: Trae sends /v1/chat/completions but GATEWAY_URL
        #    already includes /v1, so strip the prefix.
        path = self.path
        if path.startswith("/v1"):
            path = path[3:] or "/"
        if not path.startswith("/"):
            path = "/" + path
        upstream_url = GATEWAY_URL + path

        content_type = self.headers.get("Content-Type", "application/json")
        method = self.command
        headers = self._build_headers(content_type, vk, jwt)

        # 6. Forward with retry on transient connection errors
        max_retries = 2
        last_exc = None
        for attempt in range(max_retries + 1):
            conn = None
            try:
                log.debug("Upstream attempt %d: %s %s", attempt + 1, method, self.path)
                conn = self._open_upstream(upstream_url)
                parsed = urlparse(upstream_url)
                req_path = parsed.path
                if parsed.query:
                    req_path += "?" + parsed.query
                conn.request(method, req_path, body=body, headers=headers)

                # Wait for response headers: AI inference may take a long
                # time before the first byte, so use the full keepalive
                # window rather than the short socket timeout.
                if conn.sock:
                    conn.sock.settimeout(self.server.keepalive_idle)
                resp = conn.getresponse()

                # Send response headers to client
                self.send_response(resp.status)
                for key, val in resp.getheaders():
                    if key.lower() in ("transfer-encoding", "connection"):
                        continue
                    self.send_header(key, val)
                self.send_header("Connection", "close")
                self.end_headers()

                # Stream response body with keepalive
                try:
                    self._read_with_keepalive(conn, resp)
                except BrokenPipeError:
                    log.warning("Client disconnected mid-stream")
                    return
                except TimeoutError as e:
                    log.error("%s", e)
                    return

                last_exc = None
                break

            except (ConnectionResetError, ConnectionAbortedError) as e:
                last_exc = e
                log.warning("Connection reset on attempt %d/%d: %s",
                            attempt + 1, max_retries + 1, e)
                if attempt >= max_retries:
                    break
                time.sleep(0.5 * (attempt + 1))
                continue

            except (socket.timeout, TimeoutError) as e:
                log.error("Upstream timeout: %s", e)
                try:
                    self._send_json(504, {"error": f"Upstream timeout: {e}"})
                except BrokenPipeError:
                    pass
                return

            except Exception as e:
                log.exception("Proxy error")
                try:
                    self._send_json(502, {"error": f"Proxy error: {e}"})
                except BrokenPipeError:
                    pass
                return

            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        # All retries exhausted
        if last_exc:
            try:
                self._send_json(502, {"error": f"Proxy error: {last_exc}"})
            except BrokenPipeError:
                pass

    def _handle_health(self) -> None:
        cfg_vk = self.server.config_vk
        with _jwt_lock:
            jwt_present = bool(_cached_jwt)
            expires_in = int(_cached_jwt_exp - time.time()) if jwt_present else 0
        self._send_json(200, {
            "status": "ok",
            "vk_configured": bool(cfg_vk),
            "vk_preview": (cfg_vk[:8] + "...") if cfg_vk else None,
            "jwt_cached": jwt_present,
            "jwt_expires_in": expires_in,
            "gateway": GATEWAY_URL,
            "keepalive_idle": self.server.keepalive_idle,
            "socket_timeout": self.server.socket_timeout,
        })


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that carries proxy-wide config."""

    def __init__(self, addr, handler, config_vk, keepalive_idle, socket_timeout):
        super().__init__(addr, handler)
        self.config_vk = config_vk
        self.keepalive_idle = keepalive_idle
        self.socket_timeout = socket_timeout
        self.daemon_threads = True


# ── CLI / entry point ───────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cannbot-proxy",
        description="Local HTTP proxy that injects CANNBOT auth headers for Trae IDE.",
    )
    p.add_argument("--vk", help="CANNBOT Virtual Key (vk-xxxx). Overrides $CANNBOT_VK.")
    p.add_argument("--port", type=int, help="Listen port (default 8765).")
    p.add_argument("--host", help="Bind address (default 127.0.0.1).")
    p.add_argument("--log-level", help="DEBUG/INFO/WARNING/ERROR.")
    p.add_argument("--log", help="Also write logs to this file.")
    p.add_argument("--daemon", action="store_true",
                   help="Fork into background (POSIX only).")
    return p.parse_args()


def _resolve_config(args) -> Tuple[str, str, int, str, int, int]:
    vk = args.vk or os.environ.get("CANNBOT_VK", "")
    if not vk:
        fallback = os.path.expanduser("~/.cannbot/vk")
        if os.path.isfile(fallback):
            with open(fallback, "r", encoding="utf-8") as f:
                vk = f.read().strip()
    if not vk:
        sys.stderr.write(
            "ERROR: No Virtual Key configured.\n"
            "  Set --vk vk-xxxx, or $CANNBOT_VK, or write to ~/.cannbot/vk.\n"
        )
        sys.exit(2)

    host = args.host or os.environ.get("CANNBOT_PROXY_HOST", DEFAULT_HOST)
    port = args.port or int(os.environ.get("CANNBOT_PROXY_PORT", DEFAULT_PORT))
    log_level = args.log_level or os.environ.get("CANNBOT_LOG_LEVEL", DEFAULT_LOG_LEVEL)
    keepalive_idle = int(os.environ.get("CANNBOT_KEEPALIVE_IDLE", DEFAULT_KEEPALIVE_IDLE))
    socket_timeout = int(os.environ.get("CANNBOT_SOCKET_TIMEOUT", DEFAULT_SOCKET_TIMEOUT))
    return vk, host, port, log_level, keepalive_idle, socket_timeout


def _daemonize(log_file: Optional[str]) -> None:
    """Classic double-fork detach (POSIX)."""
    if os.name != "posix":
        sys.stderr.write("Daemon mode is POSIX only; run without --daemon.\n")
        sys.exit(1)
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent: print and exit
        print(f"Daemon started (PID={pid}), "
              f"see {log_file or '/tmp/cannbot_proxy.log'}")
        os._exit(0)
    # Child: become session leader
    os.setsid()
    # Second fork
    pid = os.fork()
    if pid > 0:
        os._exit(0)
    # Daemon: redirect std fds
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = open(os.devnull, "rb")
    os.dup2(devnull.fileno(), 0)
    out = open(log_file or "/tmp/cannbot_proxy.log", "ab", buffering=0)
    os.dup2(out.fileno(), 1)
    os.dup2(out.fileno(), 2)


def main() -> None:
    args = _parse_args()
    vk, host, port, log_level, keepalive_idle, socket_timeout = _resolve_config(args)
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

    # Validate VK shape
    if not is_vk(vk):
        log.warning("VK does not start with 'vk-' (got %r). "
                    "If this is a JWT, the proxy will use it as the bearer "
                    "token and fall back to the configured VK for x-api-vkey.",
                    vk[:8] + "...")

    # Pre-warm JWT
    exchange_vk_for_jwt(vk)

    server = _Server(
        (host, port), ProxyHandler,
        config_vk=vk,
        keepalive_idle=keepalive_idle,
        socket_timeout=socket_timeout,
    )

    # Graceful shutdown
    def _graceful(signum, _frame):
        log.info("Caught signal %d, shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _graceful)
        except (ValueError, OSError):
            pass

    log.info("CANNBOT proxy listening on http://%s:%d", host, port)
    log.info("  Gateway       : %s", GATEWAY_URL)
    log.info("  VK            : %s", vk[:8] + "..." if len(vk) > 8 else vk)
    log.info("  Keepalive     : idle=%ds, socket_op=%ds", keepalive_idle, socket_timeout)
    log.info("Configure Trae -> API Base URL: http://%s:%d/v1", host, port)
    log.info("                    API Key    : your VK (e.g. vk-xxxx)")

    try:
        server.serve_forever()
    finally:
        server.server_close()
        log.info("Proxy stopped")


if __name__ == "__main__":
    main()
