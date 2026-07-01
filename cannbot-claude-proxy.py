#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CANNBOT gateway proxy for Claude Code.

Claude Code speaks the Anthropic Messages API (``POST /v1/messages``) and points
at a custom endpoint via ``ANTHROPIC_BASE_URL``. The CANNBOT gateway is
OpenAI-compatible and requires two headers on every request::

    x-api-vkey:    <Virtual Key, e.g. vk-xxxxxx>
    Authorization: Bearer <short-lived JWT>

The JWT is obtained by exchanging the Virtual Key at ``AUTH_URL``. This proxy
translates the request/response between Anthropic Messages and OpenAI Chat
Completions (including the streaming SSE event sequence) and injects the two
headers.

Usage::

    export CANNBOT_VK="vk-xxxxxxxxxxxxxxxxxxxx"
    python3 cannbot-claude-proxy.py

    export ANTHROPIC_BASE_URL="http://127.0.0.1:8766"
    export ANTHROPIC_MODEL="glm-5.1"

Environment (all optional except ``CANNBOT_VK``):

    CANNBOT_VK                  Virtual Key.
    CANNBOT_CLAUDE_PROXY_PORT   Listen port (default 8766).
    CANNBOT_PROXY_HOST          Bind address (default 127.0.0.1).
    CANNBOT_KEEPALIVE_IDLE      Max idle seconds before timeout (default 300).
    CANNBOT_SOCKET_TIMEOUT      Per-read socket timeout (default 30).
    CANNBOT_LOG_LEVEL           DEBUG/INFO/WARNING/ERROR (default INFO).

CLI flags override env vars: --vk, --port, --host, --log-level, --log, --daemon.
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
from typing import List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ── Defaults ───────────────────────────────────────────────────────────
GATEWAY_URL = "https://cannbot.hicann.cn/gateway/compatible-mode/v1"
AUTH_URL = "https://cannbot.hicann.cn/cannbot/api/auth/authenticate"
DEFAULT_PORT = 8766
DEFAULT_HOST = "127.0.0.1"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_KEEPALIVE_IDLE = 300
DEFAULT_SOCKET_TIMEOUT = 30

# ── Logging ─────────────────────────────────────────────────────────────
log = logging.getLogger("cannbot-claude-proxy")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _log_json(obj) -> str:
    """Compact JSON for logging, trimming over-long strings (e.g. base64 images)."""
    def trim(x):
        if isinstance(x, str) and len(x) > 2000:
            return x[:2000] + f"...(+{len(x) - 2000} chars)"
        if isinstance(x, list):
            return [trim(v) for v in x]
        if isinstance(x, dict):
            return {k: trim(v) for k, v in x.items()}
        return x
    return json.dumps(trim(obj), ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════
# JWT cache (process-wide, thread-safe)
# ═══════════════════════════════════════════════════════════════════════
_cached_jwt: Optional[str] = None
_cached_jwt_exp: float = 0.0
_cached_jwt_vk: str = ""      # which VK the cached JWT belongs to
_jwt_lock = threading.Lock()


def _jwt_is_valid() -> bool:
    return bool(_cached_jwt) and _cached_jwt_exp > time.time() + 60


def get_mac() -> str:
    """Return a non-zero MAC address if possible, else all-zeros placeholder."""
    try:
        mac_int = uuid.getnode()
        if (mac_int >> 40) % 2 == 0:  # not a random addr
            return ":".join(
                f"{(mac_int >> i) & 0xff:02x}" for i in (40, 32, 24, 16, 8, 0)
            )
    except Exception as e:
        log.debug("get_mac() failed: %s", e)
    return "00:00:00:00:00:00"


def exchange_vk_for_jwt(vk: str) -> Optional[str]:
    """Exchange a Virtual Key for a JWT access token (with caching)."""
    global _cached_jwt, _cached_jwt_exp, _cached_jwt_vk

    if not vk:
        log.error("Cannot exchange empty VK")
        return None

    with _jwt_lock:
        if vk == _cached_jwt_vk and _jwt_is_valid():
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
            _cached_jwt_vk = vk
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


def is_vk(key: Optional[str]) -> bool:
    """Return True if *key* looks like a Virtual Key (``vk-...``)."""
    return bool(key) and key.startswith("vk-")


# ── In-memory VK (may be filled lazily; not required at startup) ─────────
VK_FILE = os.path.expanduser("~/.cannbot/vk")
_runtime_vk: str = ""
_vk_lock = threading.Lock()


def _read_vk_file() -> str:
    try:
        with open(VK_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def resolve_vk(provided: Optional[str] = None) -> str:
    """Return the VK to use.

    A ``vk-`` token supplied by the client (``ANTHROPIC_AUTH_TOKEN``) is
    authoritative and overrides any cached/configured key. Otherwise use the
    in-memory cache, then ``$CANNBOT_VK``, then ``~/.cannbot/vk`` (re-read
    lazily so a VK provided after startup still works). The chosen VK is
    cached in memory.
    """
    global _runtime_vk
    with _vk_lock:
        if provided and provided.startswith("vk-"):
            _runtime_vk = provided
            return provided
        if _runtime_vk:
            return _runtime_vk
        vk = os.environ.get("CANNBOT_VK", "") or _read_vk_file()
        if vk:
            _runtime_vk = vk
        return vk


# ═══════════════════════════════════════════════════════════════════════
# Translation: Anthropic Messages API  <->  OpenAI Chat Completions
# ═══════════════════════════════════════════════════════════════════════

_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}


def _map_stop_reason(finish_reason: Optional[str]) -> str:
    return _STOP_REASON_MAP.get(finish_reason or "", "end_turn")


def _system_text(system) -> str:
    """Anthropic ``system`` may be a string or a list of text blocks."""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "".join(
            b.get("text", "") for b in system
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _tool_result_content(content) -> str:
    """Flatten an Anthropic tool_result ``content`` into an OpenAI tool string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                else:
                    parts.append(json.dumps(b, ensure_ascii=False))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _tool_choice(tc: dict):
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "tool" and tc.get("name"):
        return {"type": "function", "function": {"name": tc["name"]}}
    return "auto"


def translate_request(body: dict) -> dict:
    """Anthropic ``/v1/messages`` body -> OpenAI ``/chat/completions`` body."""
    out: dict = {"model": body.get("model"), "stream": bool(body.get("stream"))}
    messages: List[dict] = []

    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _system_text(system)})

    for msg in body.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")

        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue

        text_parts: List[dict] = []
        image_parts: List[dict] = []
        tool_calls: List[dict] = []
        tool_results: List[dict] = []

        for block in content or []:
            btype = block.get("type")
            if btype == "text":
                text_parts.append({"type": "text", "text": block.get("text", "")})
            elif btype == "image":
                src = block.get("source", {})
                if src.get("type") == "base64":
                    url = (f"data:{src.get('media_type', 'image/png')};"
                           f"base64,{src.get('data', '')}")
                    image_parts.append(
                        {"type": "image_url", "image_url": {"url": url}})
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input", {}),
                                                ensure_ascii=False),
                    },
                })
            elif btype == "tool_result":
                tool_results.append(block)

        # tool_result blocks answer prior assistant tool_calls: emit as their
        # own OpenAI ``tool`` messages, before any sibling user text.
        for tr in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tr.get("tool_use_id"),
                "content": _tool_result_content(tr.get("content")),
            })

        if role == "assistant":
            m: dict = {"role": "assistant"}
            m["content"] = "".join(p["text"] for p in text_parts) if text_parts else None
            if tool_calls:
                m["tool_calls"] = tool_calls
            if m.get("content") or m.get("tool_calls"):
                messages.append(m)
        else:  # user
            if image_parts:
                messages.append({"role": "user", "content": text_parts + image_parts})
            elif text_parts:
                messages.append({
                    "role": "user",
                    "content": "".join(p["text"] for p in text_parts),
                })

    out["messages"] = messages

    if body.get("tools"):
        out["tools"] = [{
            "type": "function",
            "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        } for t in body["tools"]]

    if body.get("tool_choice"):
        out["tool_choice"] = _tool_choice(body["tool_choice"])

    for key in ("max_tokens", "temperature", "top_p"):
        if body.get(key) is not None:
            out[key] = body[key]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]

    if out["stream"]:
        out["stream_options"] = {"include_usage": True}

    return out


def translate_response(oai: dict, model: str) -> dict:
    """Non-streaming OpenAI response -> Anthropic message object."""
    choice = (oai.get("choices") or [{}])[0]
    msg = choice.get("message") or {}

    content: List[dict] = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            parsed = json.loads(fn.get("arguments") or "{}")
        except (ValueError, TypeError):
            parsed = {}
        content.append({
            "type": "tool_use",
            "id": tc.get("id"),
            "name": fn.get("name"),
            "input": parsed,
        })
    if not content:
        content.append({"type": "text", "text": ""})

    usage = oai.get("usage") or {}
    return {
        "id": oai.get("id") or ("msg_" + uuid.uuid4().hex),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": _map_stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def estimate_tokens(body: dict) -> int:
    """Rough char-based token estimate for ``/v1/messages/count_tokens``."""
    chars = len(_system_text(body.get("system")))
    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    chars += len(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    chars += len(_tool_result_content(block.get("content")))
                elif block.get("type") == "tool_use":
                    chars += len(json.dumps(block.get("input", {})))
    return max(1, chars // 4)


def _sse(event: str, data: dict) -> bytes:
    return (f"event: {event}\n"
            f"data: {json.dumps(data, ensure_ascii=False)}\n\n").encode("utf-8")


class SSETranslator:
    """State machine turning OpenAI stream chunks into Anthropic SSE events.

    Feed each parsed OpenAI chunk to :meth:`push`; call :meth:`finish` once the
    upstream stream ends. Both return a list of ready-to-write SSE byte strings.
    Anthropic requires a single content block open at a time and sequential
    block indices spanning both text and tool_use blocks.
    """

    def __init__(self, model: str):
        self.model = model
        self.message_id = "msg_" + uuid.uuid4().hex
        self.message_started = False
        self.next_index = 0
        self.open_index: Optional[int] = None   # currently open Anthropic block
        self.open_type: Optional[str] = None    # "text" | "tool"
        self.tool_seen = set()                  # OpenAI tool_call indices opened
        self.stop_reason: Optional[str] = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.acc_text = ""                      # accumulated assistant text (for logging)
        self.acc_tools: dict = {}               # tc_index -> {"name", "arguments"}

    # -- helpers ----------------------------------------------------------
    def _start_message(self, out: List[bytes]) -> None:
        if self.message_started:
            return
        out.append(_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self.message_id,
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": self.input_tokens, "output_tokens": 0},
            },
        }))
        self.message_started = True

    def _close_open(self, out: List[bytes]) -> None:
        if self.open_index is not None:
            out.append(_sse("content_block_stop",
                            {"type": "content_block_stop", "index": self.open_index}))
            self.open_index = None
            self.open_type = None

    def _ensure_text(self, out: List[bytes]) -> None:
        if self.open_type == "text":
            return
        self._close_open(out)
        idx = self.next_index
        self.next_index += 1
        self.open_index = idx
        self.open_type = "text"
        out.append(_sse("content_block_start", {
            "type": "content_block_start",
            "index": idx,
            "content_block": {"type": "text", "text": ""},
        }))

    def _open_tool(self, out: List[bytes], tc_id: str, name: str) -> None:
        self._close_open(out)
        idx = self.next_index
        self.next_index += 1
        self.open_index = idx
        self.open_type = "tool"
        out.append(_sse("content_block_start", {
            "type": "content_block_start",
            "index": idx,
            "content_block": {"type": "tool_use", "id": tc_id, "name": name, "input": {}},
        }))

    # -- driving ----------------------------------------------------------
    def push(self, chunk: dict) -> List[bytes]:
        out: List[bytes] = []

        usage = chunk.get("usage")
        if usage:
            self.input_tokens = usage.get("prompt_tokens", self.input_tokens)
            self.output_tokens = usage.get("completion_tokens", self.output_tokens)

        self._start_message(out)

        choices = chunk.get("choices") or []
        if not choices:
            return out
        choice = choices[0]
        delta = choice.get("delta") or {}

        text = delta.get("content")
        if text:
            self.acc_text += text
            self._ensure_text(out)
            out.append(_sse("content_block_delta", {
                "type": "content_block_delta",
                "index": self.open_index,
                "delta": {"type": "text_delta", "text": text},
            }))

        for tc in delta.get("tool_calls") or []:
            tc_index = tc.get("index", 0)
            fn = tc.get("function") or {}
            if tc_index not in self.tool_seen:
                self.tool_seen.add(tc_index)
                self._open_tool(out, tc.get("id"), fn.get("name"))
            slot = self.acc_tools.setdefault(tc_index, {"name": None, "arguments": ""})
            if fn.get("name"):
                slot["name"] = fn["name"]
            args = fn.get("arguments")
            if args:
                slot["arguments"] += args
                out.append(_sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self.open_index,
                    "delta": {"type": "input_json_delta", "partial_json": args},
                }))

        if choice.get("finish_reason"):
            self.stop_reason = _map_stop_reason(choice["finish_reason"])

        return out

    def finish(self) -> List[bytes]:
        out: List[bytes] = []
        self._start_message(out)
        self._close_open(out)
        out.append(_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self.stop_reason or "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": self.output_tokens},
        }))
        out.append(_sse("message_stop", {"type": "message_stop"}))
        return out

    def result_summary(self) -> dict:
        """Assembled reply so far, for logging."""
        content: List[dict] = []
        if self.acc_text:
            content.append({"type": "text", "text": self.acc_text})
        for idx in sorted(self.acc_tools):
            t = self.acc_tools[idx]
            content.append({"type": "tool_use", "name": t["name"], "input": t["arguments"]})
        return {
            "stop_reason": self.stop_reason or "end_turn",
            "content": content,
            "usage": {"input_tokens": self.input_tokens,
                      "output_tokens": self.output_tokens},
        }


# ═══════════════════════════════════════════════════════════════════════
# HTTP handler
# ═══════════════════════════════════════════════════════════════════════
class ProxyHandler(BaseHTTPRequestHandler):
    """Anthropic-facing proxy: translates to the OpenAI gateway with keepalive."""

    server_version = "CANNBOTClaudeProxy/1.0"

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    # -- responses --------------------------------------------------------
    def _send_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, code: int, err_type: str, message: str) -> None:
        self._send_json(code, {
            "type": "error",
            "error": {"type": err_type, "message": message},
        })

    def _build_headers(self, vk: str, jwt: str, accept: str) -> dict:
        return {
            "x-api-vkey": vk,
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            "Accept": accept,
            "Connection": "close",
            "User-Agent": "CANNBOT-Claude-Proxy/1.0",
            "Accept-Encoding": "identity",
        }

    def _open_upstream(self, url: str) -> http.client.HTTPConnection:
        parsed = urlparse(url)
        timeout = min(self.server.socket_timeout, self.server.keepalive_idle)
        if parsed.scheme == "https":
            return http.client.HTTPSConnection(
                parsed.hostname, parsed.port or 443, timeout=timeout)
        return http.client.HTTPConnection(
            parsed.hostname, parsed.port or 80, timeout=timeout)

    # -- routing ----------------------------------------------------------
    def _route(self) -> None:
        if self.path == "/_health":
            self._handle_health()
            return

        content_length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(content_length) if content_length > 0 else b""

        path = self.path.split("?", 1)[0]
        if path.endswith("/v1/messages/count_tokens"):
            self._handle_count_tokens(body)
        elif path.endswith("/v1/messages"):
            self._handle_messages(body)
        else:
            self._send_error(404, "not_found_error", f"Unknown path: {path}")

    def _handle_count_tokens(self, body: bytes) -> None:
        try:
            anthropic_body = json.loads(body or b"{}")
        except ValueError:
            self._send_error(400, "invalid_request_error", "Malformed JSON body")
            return
        self._send_json(200, {"input_tokens": estimate_tokens(anthropic_body)})

    def _handle_messages(self, body: bytes) -> None:
        try:
            anthropic_body = json.loads(body or b"{}")
        except ValueError:
            self._send_error(400, "invalid_request_error", "Malformed JSON body")
            return

        model = anthropic_body.get("model", "")
        openai_body = translate_request(anthropic_body)
        stream = openai_body["stream"]

        log.info("REQUEST  model=%s stream=%s\n%s",
                 model, stream, _log_json(anthropic_body))
        log.debug("REQUEST (upstream OpenAI)\n%s", _log_json(openai_body))

        auth = self.headers.get("Authorization", "")
        provided = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
        vk = resolve_vk(provided)
        if not vk:
            self._send_error(401, "authentication_error",
                             "No VK available. Set $CANNBOT_VK, write ~/.cannbot/vk, "
                             "or pass ANTHROPIC_AUTH_TOKEN=vk-xxxx.")
            return

        jwt = exchange_vk_for_jwt(vk)
        if not jwt:
            self._send_error(401, "authentication_error",
                             "Failed to obtain JWT from VK. "
                             "Check the VK and network connectivity.")
            return

        headers = self._build_headers(
            vk, jwt, "text/event-stream" if stream else "application/json")
        upstream_url = GATEWAY_URL + "/chat/completions"
        payload = json.dumps(openai_body, ensure_ascii=False).encode("utf-8")
        parsed = urlparse(upstream_url)

        # Retry establishing the request on transient resets, before any client
        # bytes are written. Once streaming starts we cannot retry.
        max_retries = 2
        for attempt in range(max_retries + 1):
            conn = None
            try:
                conn = self._open_upstream(upstream_url)
                conn.request("POST", parsed.path, body=payload, headers=headers)
                if conn.sock:
                    conn.sock.settimeout(self.server.keepalive_idle)
                resp = conn.getresponse()

                if resp.status != 200:
                    detail = resp.read().decode("utf-8", "replace")
                    log.error("Upstream HTTP %d: %s", resp.status, detail[:500])
                    self._send_error(resp.status, "api_error",
                                     f"Gateway error {resp.status}: {detail[:500]}")
                    return

                if stream:
                    self._stream_response(conn, resp, model)
                else:
                    self._buffered_response(resp, model)
                return

            except (ConnectionResetError, ConnectionAbortedError) as e:
                log.warning("Connection reset on attempt %d/%d: %s",
                            attempt + 1, max_retries + 1, e)
                if attempt >= max_retries:
                    self._send_error(502, "api_error", f"Proxy error: {e}")
                    return
                time.sleep(0.5 * (attempt + 1))
            except (socket.timeout, TimeoutError) as e:
                log.error("Upstream timeout: %s", e)
                self._send_error(504, "api_error", f"Upstream timeout: {e}")
                return
            except BrokenPipeError:
                log.warning("Client disconnected")
                return
            except Exception as e:
                log.exception("Proxy error")
                self._send_error(502, "api_error", f"Proxy error: {e}")
                return
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def _buffered_response(self, resp, model: str) -> None:
        data = resp.read()
        try:
            oai = json.loads(data.decode("utf-8"))
        except ValueError:
            self._send_error(502, "api_error", "Gateway returned non-JSON response")
            return
        result = translate_response(oai, model)
        log.info("RESPONSE model=%s\n%s", model, _log_json(result))
        self._send_json(200, result)

    def _stream_response(self, conn, resp, model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        translator = SSETranslator(model)
        keepalive_idle = self.server.keepalive_idle
        socket_timeout = self.server.socket_timeout
        last_data = time.time()
        buffer = b""

        try:
            while True:
                if time.time() - last_data > keepalive_idle:
                    raise TimeoutError(f"Keepalive timeout: no data for {keepalive_idle}s")
                remaining = keepalive_idle - (time.time() - last_data)
                if conn.sock:
                    conn.sock.settimeout(min(socket_timeout, remaining))
                try:
                    chunk = resp.read1(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                last_data = time.time()
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self._write_events(self._parse_sse_line(line, translator))
            if buffer.strip():
                self._write_events(self._parse_sse_line(buffer, translator))
            self._write_events(translator.finish())
        except BrokenPipeError:
            log.warning("Client disconnected mid-stream")
        except TimeoutError as e:
            log.error("%s", e)
        finally:
            log.info("RESPONSE model=%s (stream)\n%s",
                     model, _log_json(translator.result_summary()))

    def _parse_sse_line(self, line: bytes, translator: SSETranslator) -> List[bytes]:
        line = line.strip()
        if not line or not line.startswith(b"data:"):
            return []
        payload = line[len(b"data:"):].strip()
        if payload == b"[DONE]":
            return []
        try:
            obj = json.loads(payload)
        except ValueError:
            return []
        return translator.push(obj)

    def _write_events(self, events: List[bytes]) -> None:
        if not events:
            return
        for ev in events:
            self.wfile.write(ev)
        self.wfile.flush()

    def _handle_health(self) -> None:
        vk = resolve_vk()
        with _jwt_lock:
            jwt_present = bool(_cached_jwt)
            expires_in = int(_cached_jwt_exp - time.time()) if jwt_present else 0
        self._send_json(200, {
            "status": "ok",
            "vk_configured": bool(vk),
            "vk_preview": (vk[:8] + "...") if vk else None,
            "jwt_cached": jwt_present,
            "jwt_expires_in": expires_in,
            "gateway": GATEWAY_URL,
            "keepalive_idle": self.server.keepalive_idle,
            "socket_timeout": self.server.socket_timeout,
        })


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying proxy-wide config."""

    def __init__(self, addr, handler, keepalive_idle, socket_timeout):
        super().__init__(addr, handler)
        self.keepalive_idle = keepalive_idle
        self.socket_timeout = socket_timeout
        self.daemon_threads = True


# ═══════════════════════════════════════════════════════════════════════
# CLI / entry point
# ═══════════════════════════════════════════════════════════════════════
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cannbot-claude-proxy",
        description="Local proxy bridging Claude Code to the CANNBOT gateway.",
    )
    p.add_argument("--vk", help="CANNBOT Virtual Key (vk-xxxx). Overrides $CANNBOT_VK.")
    p.add_argument("--port", type=int, help="Listen port (default 8766).")
    p.add_argument("--host", help="Bind address (default 127.0.0.1).")
    p.add_argument("--log-level", help="DEBUG/INFO/WARNING/ERROR.")
    p.add_argument("--log", help="Also write logs to this file.")
    p.add_argument("--daemon", action="store_true",
                   help="Fork into background (POSIX only).")
    return p.parse_args()


def _persist_vk(path: str, vk: str) -> None:
    """Write *vk* to *path* (0600) unless it already matches — best effort."""
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                if f.read().strip() == vk:
                    return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(vk + "\n")
        os.chmod(path, 0o600)
    except OSError as e:
        sys.stderr.write(f"WARNING: could not update saved VK at {path}: {e}\n")


def _resolve_config(args) -> Tuple[str, str, int, str, int, int]:
    # Precedence: --vk flag > $CANNBOT_VK env > saved ~/.cannbot/vk file.
    # VK is optional at startup — the proxy resolves it lazily per request.
    if args.vk:
        vk, vk_source = args.vk, "flag"
    elif os.environ.get("CANNBOT_VK"):
        vk, vk_source = os.environ["CANNBOT_VK"], "env"
    else:
        vk, vk_source = _read_vk_file(), "file"

    # An explicitly supplied VK (env var or flag) updates the saved copy, so the
    # background service picks up a rotated key on its next launch.
    if vk and vk_source in ("env", "flag"):
        _persist_vk(VK_FILE, vk)

    host = args.host or os.environ.get("CANNBOT_PROXY_HOST", DEFAULT_HOST)
    port = args.port or int(os.environ.get("CANNBOT_CLAUDE_PROXY_PORT", DEFAULT_PORT))
    log_level = args.log_level or os.environ.get("CANNBOT_LOG_LEVEL", DEFAULT_LOG_LEVEL)
    keepalive_idle = int(os.environ.get("CANNBOT_KEEPALIVE_IDLE", DEFAULT_KEEPALIVE_IDLE))
    socket_timeout = int(os.environ.get("CANNBOT_SOCKET_TIMEOUT", DEFAULT_SOCKET_TIMEOUT))
    return vk, host, port, log_level, keepalive_idle, socket_timeout


def _daemonize(log_file: Optional[str]) -> None:
    """Classic double-fork detach (POSIX)."""
    if os.name != "posix":
        sys.stderr.write("Daemon mode is POSIX only; run without --daemon.\n")
        sys.exit(1)
    pid = os.fork()
    if pid > 0:
        print(f"Daemon started (PID={pid}), "
              f"see {log_file or '/tmp/cannbot_claude_proxy.log'}")
        os._exit(0)
    os.setsid()
    pid = os.fork()
    if pid > 0:
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = open(os.devnull, "rb")
    os.dup2(devnull.fileno(), 0)
    out = open(log_file or "/tmp/cannbot_claude_proxy.log", "ab", buffering=0)
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
        pid_path = os.path.expanduser("~/.cannbot/proxy/claude-proxy.pid")
        os.makedirs(os.path.dirname(pid_path), exist_ok=True)
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))

    global _runtime_vk
    if vk:
        _runtime_vk = vk
        if not is_vk(vk):
            log.warning("VK does not start with 'vk-' (got %r).", vk[:8] + "...")
        exchange_vk_for_jwt(vk)  # pre-warm
    else:
        log.warning("No VK at startup; will resolve from $CANNBOT_VK / "
                    "~/.cannbot/vk / request header on first request.")

    server = _Server(
        (host, port), ProxyHandler,
        keepalive_idle=keepalive_idle,
        socket_timeout=socket_timeout,
    )

    def _graceful(signum, _frame):
        log.info("Caught signal %d, shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _graceful)
        except (ValueError, OSError):
            pass

    log.info("CANNBOT Claude proxy listening on http://%s:%d", host, port)
    log.info("  Gateway   : %s", GATEWAY_URL)
    log.info("  VK        : %s", (vk[:8] + "...") if vk else "(deferred)")
    log.info("  Keepalive : idle=%ds, socket_op=%ds", keepalive_idle, socket_timeout)
    log.info("Point Claude Code at it:")
    log.info("  export ANTHROPIC_BASE_URL=http://%s:%d", host, port)
    log.info("  export ANTHROPIC_MODEL=glm-5.1")

    try:
        server.serve_forever()
    finally:
        server.server_close()
        log.info("Proxy stopped")


if __name__ == "__main__":
    main()
