"""Portability tests - the per-OS Bitwig-root probe and the render backend dispatch,
all pure/filesystem-level (no live Bitwig, no sound server needed)."""
import pytest

import openwig.song as song
import openwig.wire.render as render
from openwig.song import _find_bitwig_root, _probe_roots
from openwig.wire.render import _parse_default_sink


# -- _probe_roots -------------------------------------------------------------------

def test_probe_roots_prefers_candidate_with_factory_devices(tmp_path):
    plain = tmp_path / "plain"; plain.mkdir()
    real = tmp_path / "real"; (real / "Library" / "devices").mkdir(parents=True)
    # the candidate carrying Library/devices wins even when listed after one that exists
    assert _probe_roots([str(plain), str(real)]) == str(real).replace("\\", "/")


def test_probe_roots_falls_back_to_first_existing_dir(tmp_path):
    missing = tmp_path / "missing"
    existing = tmp_path / "existing"; existing.mkdir()
    assert _probe_roots([str(missing), str(existing)]) == str(existing).replace("\\", "/")


def test_probe_roots_falls_back_to_first_candidate_when_nothing_exists(tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"
    # nothing exists: return the first (conventional) location so errors name it
    assert _probe_roots([str(a), str(b)]) == str(a).replace("\\", "/")


# -- _find_bitwig_root --------------------------------------------------------------

def test_bitwig_path_env_overrides_everything(monkeypatch):
    monkeypatch.setenv("BITWIG_PATH", r"D:\Custom\Bitwig\\")
    assert _find_bitwig_root(platform="linux") == "D:/Custom/Bitwig"


def test_linux_root_defaults_to_opt_when_nothing_installed(monkeypatch):
    monkeypatch.delenv("BITWIG_PATH", raising=False)
    # on a machine with no linux Bitwig install the conventional deb location is named
    assert _find_bitwig_root(platform="linux") == "/opt/bitwig-studio"


def test_darwin_root_defaults_to_app_bundle(monkeypatch):
    monkeypatch.delenv("BITWIG_PATH", raising=False)
    root = _find_bitwig_root(platform="darwin")
    assert root.startswith("/Applications/Bitwig Studio.app/Contents")


# -- render backend dispatch ---------------------------------------------------------

def test_render_unsupported_platform_raises(monkeypatch):
    monkeypatch.setattr(render.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="not supported"):
        render.render_to_wav(object(), "out.wav")


def test_parse_default_sink_extracts_name():
    out = ("Server String: /run/user/1000/pulse/native\n"
           "Default Sample Specification: s16le 2ch 48000Hz\n"
           "Default Sink: alsa_output.pci-0000_00_1f.3.analog-stereo\n"
           "Default Source: alsa_input.pci-0000_00_1f.3.analog-stereo\n")
    assert _parse_default_sink(out) == "alsa_output.pci-0000_00_1f.3.analog-stereo"


def test_parse_default_sink_handles_missing_line():
    assert _parse_default_sink("Server Name: pulseaudio\n") is None
    assert _parse_default_sink("") is None
    assert _parse_default_sink(None) is None
