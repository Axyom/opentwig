"""Controller syntax test (UNIT, not live).

Verifies the bundled controller script `openwig_bridge.control.js` is syntactically
valid JavaScript. If node is on PATH we run `node --check` (the real parser check that
catches controller syntax errors in CI). If node is absent we skip the parse check but
still do a cheap structural sanity check that does not need any JS engine.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

CONTROLLER = (
    Path(__file__).resolve().parents[1]
    / "src" / "openwig" / "controller" / "openwig_bridge.control.js"
)


def test_controller_file_exists():
    assert CONTROLLER.is_file(), f"controller script not found at {CONTROLLER}"


def test_controller_is_the_bridge_controller():
    """Structural sanity (no JS engine needed): it is the OpenwigBridge controller."""
    text = CONTROLLER.read_text(encoding="utf-8")
    assert "host.defineController(" in text, "missing host.defineController( call"
    assert "var HANDLERS = {" in text, "missing the HANDLERS dispatch table"


def test_controller_is_valid_javascript():
    """Parse the controller with node --check; skip if no node engine is installed."""
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("node not available")
    proc = subprocess.run(
        [node, "--check", str(CONTROLLER)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"node --check reported a syntax error in {CONTROLLER}:\n{proc.stderr}"
    )
