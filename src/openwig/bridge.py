#!/usr/bin/env python3
"""
bridge_client.py - TCP client for the Bitwig MCP controller-script bridge.

Talks to openwig_bridge.control.js (running inside Bitwig) over a plain TCP
socket using newline-delimited JSON-RPC 2.0.  The controller script is the
TCP *server* (it calls host.createRemoteConnection); this is the client.

Framing: each message is one line of compact JSON terminated by '\n'.
JSON.stringify / json.dumps never emit a raw newline, and 0x0A never occurs
inside a UTF-8 multibyte sequence, so splitting the byte stream on '\n' is a
safe framer in both directions.

Usage:
    from bridge_client import BridgeClient
    bridge = BridgeClient()
    bridge.start()                       # background connect + reconnect
    bridge.request("transport.play")
    state = bridge.request("state.snapshot")

The client auto-reconnects: Bitwig can be (re)started at any time and the
next request will succeed once the controller is listening again.
"""

import itertools
import json
import socket
import struct
import threading
import time
from concurrent.futures import Future

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7777


class BridgeError(RuntimeError):
    """Raised when a request fails or the bridge is unreachable."""


class IncompatibleBitwigVersion(BridgeError):
    """Raised when the live Bitwig Studio version is not supported by this SDK.

    This SDK is locked to a specific set of Bitwig versions (see
    `openwig.SUPPORTED_BITWIG_VERSIONS`). The lock exists because the SDK
    reaches into private Bitwig internals via reflection; a Bitwig point-release
    can rename or remove the symbols we rely on without warning. Bump the SDK
    deliberately when you bump Bitwig.
    """

    def __init__(self, found: str, supported):
        self.found = found
        self.supported = tuple(sorted(supported))
        supported_str = ", ".join(f"{v}.x" for v in self.supported)
        super().__init__(
            f"Bitwig Studio {found!r} is not supported by this version of openwig. "
            f"This SDK supports: {supported_str}. "
            f"Either install a matching Bitwig version or upgrade openwig."
        )


def _frame(obj):
    """Frame an outbound message for Bitwig's RemoteSocket: 4-byte big-endian
    length prefix + UTF-8 JSON body. Bitwig reads the length, then that many
    bytes, then fires the controller's receive callback with the de-framed body.
    (Inbound from Bitwig is asymmetric: raw newline-delimited JSON, no prefix.)"""
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(body)) + body


class BridgeClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 request_timeout=5.0, connect_timeout=3.0):
        self.host = host
        self.port = port
        self.request_timeout = request_timeout
        self.connect_timeout = connect_timeout

        self._sock = None
        self._sock_lock = threading.Lock()      # serializes sends + socket swap
        self._pending = {}                       # id -> Future
        self._pending_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._connected = threading.Event()
        # set once the controller's "connected" handshake notification arrives,
        # which proves its receive callback is registered (avoids a send race)
        self._ready = threading.Event()
        self._running = False
        self._thread = None
        self._last_snapshot = None

        # optional callback(method, params) for server-pushed notifications
        self.on_notification = None

        # async-op completion: the controller PUSHES an "op_done" notification when each
        # document-thread op finishes; request_op waits on this instead of polling (polling
        # during an op runs JS on two threads at once and crashes GraalJS).
        self._ops_done = 0
        self._ops_cv = threading.Condition()
        self._push_cap = None            # None=unknown, True/False once probed

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        """Begin the background connect/reconnect loop (idempotent)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._connect_loop, name="bridge-client", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._connected.clear()
        with self._sock_lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    @property
    def connected(self):
        return self._connected.is_set()

    def wait_connected(self, timeout=None):
        # "ready" = controller handshake received, safe to send
        return self._ready.wait(timeout)

    def _push_capable(self):
        """Whether the controller pushes op_done notifications. Probed once via the ops.done
        handler (a plain read, done when no op is in flight - safe). Cached."""
        if self._push_cap is None:
            try:
                self.request("ops.done", timeout=2.0)
                self._push_cap = True
            except BridgeError:
                self._push_cap = False
        return self._push_cap

    def request_op(self, method, params=None, *, fallback=0.5, floor=0.12, timeout=8.0):
        """Fire an async document-thread op (clip create, automation write, ...) and block
        until the controller PUSHES its completion - instead of sleeping a fixed, padded
        delay. Crucially we do NOT poll while the op runs (that executes JS on a second
        thread and crashes GraalJS); we wait passively on the pushed `op_done`. Waits exactly
        as long as the op needs, plus at least `floor` s of breathing room so we don't fire
        faster than Bitwig's document machinery can absorb. Falls back to sleeping `fallback`
        on an older controller that doesn't push."""
        if not self._push_capable():
            res = self.request(method, params)
            time.sleep(fallback)
            return res
        with self._ops_cv:
            base = self._ops_done
        t0 = time.time()
        res = self.request(method, params)
        with self._ops_cv:
            self._ops_cv.wait_for(lambda: self._ops_done > base, timeout=timeout)
        rest = floor - (time.time() - t0)        # breathing room even if completion was instant
        if rest > 0:
            time.sleep(rest)
        return res

    def _device_count(self):
        """Loaded-device count on the cursor track, or None on an older controller."""
        try:
            r = self.request("track.device_count", timeout=2.0)
        except BridgeError:
            return None
        return r.get("count") if isinstance(r, dict) else r

    def request_insert(self, method, params=None, *, fallback=1.0, floor=0.15, timeout=8.0, poll=0.04):
        """Fire a device insert and wait until the device finishes loading (the cursor track's
        device count goes up) instead of sleeping a fixed second. Device loading happens in
        Bitwig's engine - NOT a GraalJS document-thread op - so polling track.device_count
        here is safe (no concurrent JS). Falls back to sleeping `fallback` on older
        controllers that don't expose the count."""
        base = self._device_count()
        t0 = time.time()
        res = self.request(method, params)
        if base is None:
            time.sleep(fallback)
            return res
        deadline = t0 + timeout
        while time.time() < deadline:
            d = self._device_count()
            if isinstance(d, (int, float)) and d > base:
                break
            time.sleep(poll)
        rest = floor - (time.time() - t0)        # breathing room
        if rest > 0:
            time.sleep(rest)
        return res

    def host_version(self):
        """Return the live Bitwig Studio version string (e.g. ``"6.0.6"``)."""
        info = self.request("host.version")
        return (info or {}).get("version")

    def ensure_compatible(self, supported_versions):
        """Raise IncompatibleBitwigVersion if the live Bitwig version is not in
        the supplied set. Call after the bridge is connected; the SDK's high-level
        entry points (e.g. ``Song.__init__``) invoke this automatically."""
        if not supported_versions:
            return  # caller opted out
        found = self.host_version() or "<unknown>"
        found_major = found.split(".")[0]
        if found_major not in supported_versions:
            raise IncompatibleBitwigVersion(found, supported_versions)
        return found

    @property
    def last_snapshot(self):
        return self._last_snapshot

    # ── connect / read loop ──────────────────────────────────────────────────

    def _connect_loop(self):
        backoff = 0.5
        while self._running:
            try:
                sock = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout)
                sock.settimeout(None)
                with self._sock_lock:
                    self._sock = sock
                self._connected.set()
                backoff = 0.5
                self._read_loop(sock)            # blocks until disconnect
            except OSError:
                pass
            finally:
                self._connected.clear()
                self._ready.clear()
                self._fail_all_pending(BridgeError("bridge disconnected"))
                with self._sock_lock:
                    if self._sock is not None:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
            if not self._running:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    def _read_loop(self, sock):
        buf = b""
        # Handshake watchdog: a live controller sends a "connected" notification right
        # after accepting us. If it doesn't arrive within a few seconds we've reached a
        # stale/zombie listener (e.g. one left briefly when Bitwig auto-reloads the
        # script on file change and rebinds the port) - drop it and let the connect loop
        # retry until it hits the live listener. Once the handshake lands, go fully
        # blocking as before.
        armed = True
        sock.settimeout(3.0)
        deadline = time.time() + 6.0
        while self._running:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                if armed and not self._ready.is_set() and time.time() > deadline:
                    break                        # no handshake -> zombie; reconnect
                continue
            except OSError:
                break
            if not chunk:
                break                            # peer closed
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self._dispatch(line.decode("utf-8", "replace"))
            if armed and self._ready.is_set():
                sock.settimeout(None); armed = False  # handshake done - block normally

    def _dispatch(self, line):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        msg_id = msg.get("id")
        if msg_id is None:
            # notification
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "connected" and isinstance(params, dict):
                self._last_snapshot = params
                self._ready.set()
            if method == "op_done":
                with self._ops_cv:
                    n = params.get("done") if isinstance(params, dict) else None
                    self._ops_done = n if isinstance(n, (int, float)) else self._ops_done + 1
                    self._push_cap = True
                    self._ops_cv.notify_all()
            if self.on_notification:
                try:
                    self.on_notification(method, params)
                except Exception:  # noqa: BLE001 - never let a callback kill the reader
                    pass
            return
        with self._pending_lock:
            fut = self._pending.pop(msg_id, None)
        if fut is None or fut.done():
            return
        if "error" in msg and msg["error"] is not None:
            err = msg["error"]
            fut.set_exception(BridgeError(
                f"{err.get('message', 'error')} (code {err.get('code')})"))
        else:
            fut.set_result(msg.get("result"))

    def _fail_all_pending(self, exc):
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    # ── request ──────────────────────────────────────────────────────────────

    def request(self, method, params=None, timeout=None):
        """Send a JSON-RPC request and block for the result.

        Raises BridgeError if the bridge is not connected within
        connect_timeout, or if the controller returns an error / times out.
        """
        timeout = self.request_timeout if timeout is None else timeout
        # wait for the controller handshake, not just TCP, so the controller's
        # receive callback is registered before we send (else the frame is lost)
        if not self._ready.wait(self.connect_timeout):
            raise BridgeError(
                f"Bitwig MCP bridge not connected at {self.host}:{self.port}. "
                "Is Bitwig running with the OpenwigBridge controller enabled?")

        msg_id = next(self._ids)
        fut = Future()
        with self._pending_lock:
            self._pending[msg_id] = fut

        payload = _frame(
            {"jsonrpc": "2.0", "id": msg_id, "method": method,
             "params": params or {}})

        with self._sock_lock:
            sock = self._sock
            if sock is None:
                with self._pending_lock:
                    self._pending.pop(msg_id, None)
                raise BridgeError("bridge socket closed mid-send")
            try:
                sock.sendall(payload)
            except OSError as e:
                with self._pending_lock:
                    self._pending.pop(msg_id, None)
                raise BridgeError(f"send failed: {e}") from e

        try:
            return fut.result(timeout=timeout)
        except TimeoutError as e:
            with self._pending_lock:
                self._pending.pop(msg_id, None)
            raise BridgeError(
                f"request '{method}' timed out after {timeout}s") from e

    def notify(self, method, params=None):
        """Fire-and-forget (no id, no response expected)."""
        if not self._connected.is_set():
            raise BridgeError("bridge not connected")
        payload = _frame({"jsonrpc": "2.0", "method": method, "params": params or {}})
        with self._sock_lock:
            if self._sock is None:
                raise BridgeError("bridge socket closed")
            self._sock.sendall(payload)


# ── CLI smoke test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    b = BridgeClient()
    b.start()
    print(f"connecting to {b.host}:{b.port} ...", file=sys.stderr)
    if not b.wait_connected(5.0):
        print("NOT CONNECTED -- is Bitwig running with OpenwigBridge enabled?",
              file=sys.stderr)
        sys.exit(1)

    method = sys.argv[1] if len(sys.argv) > 1 else "ping"
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    try:
        result = b.request(method, params)
        print(json.dumps(result, indent=2))
    except BridgeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
