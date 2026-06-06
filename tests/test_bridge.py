"""bridge_client framing + request correlation (no Bitwig; fake controller socket)."""
import json
import socket
import struct
import threading
import pytest
from openwig.bridge import BridgeClient, BridgeError, _frame


def test_frame_is_be_length_prefixed():
    f = _frame({"a": 1})
    body = json.dumps({"a": 1}, separators=(",", ":")).encode()
    assert f == struct.pack(">I", len(body)) + body


def _fake_controller(srv, received):
    try:
        conn, _ = srv.accept()
    except OSError:
        return
    # controller pushes a "connected" notification on accept (newline JSON)
    conn.sendall((json.dumps({"jsonrpc": "2.0", "method": "connected",
                              "params": {"ok": True}}) + "\n").encode())
    buf = b""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 4:
                n = struct.unpack_from(">I", buf, 0)[0]
                if len(buf) < 4 + n:
                    break
                msg = json.loads(buf[4:4 + n]); buf = buf[4 + n:]
                received.append((msg["method"], msg.get("params")))
                mid = msg.get("id")
                if mid is None:
                    continue
                if msg["method"] == "ping":
                    reply = {"jsonrpc": "2.0", "id": mid, "result": "pong"}
                elif msg["method"] == "boom":
                    reply = {"jsonrpc": "2.0", "id": mid,
                             "error": {"code": -32000, "message": "kaboom"}}
                else:
                    reply = {"jsonrpc": "2.0", "id": mid, "result": msg.get("params")}
                conn.sendall((json.dumps(reply) + "\n").encode())
    except OSError:
        pass
    finally:
        conn.close()


@pytest.fixture
def bridge():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))                 # OS-assigned free port
    port = srv.getsockname()[1]
    srv.listen(1)
    received = []
    t = threading.Thread(target=_fake_controller, args=(srv, received), daemon=True)
    t.start()
    b = BridgeClient(port=port); b.start()
    assert b.wait_connected(3), "client never got the connected handshake"
    b._received = received
    yield b
    b.stop()
    try:
        srv.close()
    except OSError:
        pass


def test_request_response_roundtrip(bridge):
    assert bridge.request("ping") == "pong"


def test_params_propagate(bridge):
    p = {"index": 0, "value": 0.5}
    assert bridge.request("track.set_volume", p) == p
    assert ("track.set_volume", p) in bridge._received


def test_error_propagates(bridge):
    with pytest.raises(BridgeError):
        bridge.request("boom")


def test_concurrent_ids_correlate(bridge):
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        out = [f.result() for f in [ex.submit(bridge.request, "echo", {"i": i}) for i in range(30)]]
    assert sorted(o["i"] for o in out) == list(range(30))


def test_connected_notification_seeds_snapshot(bridge):
    assert bridge.last_snapshot == {"ok": True}
