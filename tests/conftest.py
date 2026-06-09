"""pytest bootstrap.

Makes the in-tree src/ layout importable without `pip install -e .`, registers the
`live` marker, auto-skips every live test unless OPENWIG_LIVE=1, and exposes a
`live_bridge` fixture for the tests that talk to a real Bitwig.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: tests that require a running Bitwig Studio with the OpenwigBridge "
        "controller enabled (opt-in via OPENWIG_LIVE=1).",
    )


def pytest_collection_modifyitems(config, items):
    """Skip every test marked `live` unless OPENWIG_LIVE == "1".

    This keeps the live auto-adaptability tests from ever touching a real Bitwig in a
    normal `pytest` run (and in CI, where OPENWIG_LIVE is unset).
    """
    if os.environ.get("OPENWIG_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test (set OPENWIG_LIVE=1 to run)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def live_bridge():
    """A connected BridgeClient against the real Bitwig (live tests only).

    Constructs the client, starts the background connect loop, and waits for the
    controller handshake. If it cannot connect within ~6s the test is skipped (rather
    than failed), so a missing Bitwig is a non-event. Stops the client on teardown.
    """
    from openwig.bridge import BridgeClient

    b = BridgeClient()
    b.start()
    try:
        if not b.wait_connected(6.0):
            pytest.skip("no live Bitwig bridge on 127.0.0.1:7777 (controller not connected)")
        yield b
    finally:
        b.stop()
