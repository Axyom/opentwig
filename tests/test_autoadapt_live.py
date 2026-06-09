"""Live auto-adaptability tests (opt-in, marked `live`).

These verify, against a REAL running Bitwig, that the resolver still resolves Bitwig's
obfuscated internals at runtime: it can write arranger automation, create clips, read the
document descriptor, serialize, and normalize, with and without name seeds (blind mode),
and that a normal probe writes a build-keyed symbol cache.

They are skipped unless OPENWIG_LIVE=1 (see conftest.pytest_collection_modifyitems), so a
normal `pytest` run never touches the live Bitwig.

Each test stands up a throwaway probe track by INDEX-DIFF (mirroring
diagnostics.run_selftest): snapshot the occupied indices, create an instrument track, poll
state.snapshot until a NEW occupied slot appears, select it, run the probe, then in a
finally delete every slot that appeared (covers a failed post-create rename), highest index
first so earlier indices stay valid.
"""
import os
import struct
import tempfile
import time
import wave
from pathlib import Path

import pytest

PROBE_TRACK = "__openwig_probe_test__"


def _write_silent_wav(path, seconds=0.1):
    """Write a tiny silent mono WAV (mirrors diagnostics._write_silent_wav)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(44100 * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(struct.pack("<" + "h" * n, *([0] * n)))


def _occupied(b):
    """Set of main-track indices currently occupied (by name; the exists flag is flaky)."""
    snap = b.request("state.snapshot")
    return {t.get("index") for t in snap.get("tracks", []) if t.get("name")}


def _make_probe_track(b):
    """Create + select a throwaway instrument track, returning (selected_index, before_set).

    Raises AssertionError if the new slot never appears. The caller is responsible for
    cleaning up via _cleanup_probe_tracks(b, before) in a finally.
    """
    before = _occupied(b)
    b.request("track.create", {"type": "instrument", "name": PROBE_TRACK})
    idx = None
    for _ in range(16):
        time.sleep(0.25)
        new_indices = sorted(_occupied(b) - before)
        if new_indices:
            idx = new_indices[-1]
            break
    assert idx is not None, "probe track did not appear (cannot run round-trip)"
    b.request("track.select", {"index": idx})
    time.sleep(0.3)
    return idx, before


def _cleanup_probe_tracks(b, before):
    """Delete every slot that appeared since `before`, highest index first."""
    try:
        appeared = sorted(_occupied(b) - before, reverse=True)
    except Exception:  # noqa: BLE001 - cleanup must never mask the test result
        return
    for idx in appeared:
        try:
            b.request("track.delete", {"index": idx})
        except Exception:  # noqa: BLE001
            pass


pytestmark = pytest.mark.live


def test_probe_normal_resolves_and_verifies(live_bridge):
    """A normal (name-seeded) probe resolves and verifies every reflection path."""
    b = live_bridge
    _, before = _make_probe_track(b)
    try:
        b.request_op("resolver.probe", {"blind": False}, timeout=90)
        res = b.request("resolver.result", timeout=10)
        report = res.get("report")
        assert report is not None, f"no probe report (error={res.get('error')!r})"

        assert report.get("ok") is True, f"report not ok: {report}"

        caps = report["capabilities"]
        for name in ("automation_write", "clip_create", "descriptor_read",
                     "serialize", "normalize"):
            assert caps[name]["ok"] is True, f"capability {name} failed: {caps[name]}"

        reader = report.get("reader")
        assert reader is not None, "normal probe did not resolve the descriptor reader"
        assert set(reader.keys()) == {"mX_", "KRt", "bf", "ngq", "nI_", "Xzy", "uEK"}, (
            f"reader keys unexpected: {sorted(reader.keys())}"
        )

        commands = report.get("commands")
        assert commands is not None, "normal probe did not resolve commands"
        assert commands.get("resolved") is True, f"commands not resolved: {commands}"
        for cmd in ("clipCmd", "noteCmd"):
            spec = commands[cmd]
            for field in ("cls", "field", "factory", "exec"):
                assert spec.get(field), f"commands.{cmd} missing {field}: {spec}"
    finally:
        _cleanup_probe_tracks(b, before)


def test_probe_blind_discovers_structurally(live_bridge):
    """Blind mode disables ALL name seeds to prove the discovery MECHANISM is name-free.

    It asserts the guarantees pure structural discovery actually provides on any build:
    - arranger automation write resolves and verifies with zero name hints,
    - the descriptor reader skeleton resolves (mX_ / ngq / uEK) and the walk runs,
    - an arranger clip + note is created (the command path works),
    - it does not write the cache.

    It deliberately does NOT require the clip note to be read back: a pure structural
    reader is automation-complete but may pick a non-canonical (note-incomplete) traversal.
    Note read-back is guaranteed on real builds by the seed-first / cache path (covered by
    test_probe_normal_resolves_and_verifies), not by the blind stress path.
    """
    b = live_bridge
    _, before = _make_probe_track(b)
    try:
        b.request_op("resolver.probe", {"blind": True}, timeout=120)
        res = b.request("resolver.result", timeout=10)
        report = res.get("report")
        assert report is not None, f"no blind probe report (error={res.get('error')!r})"

        caps = report["capabilities"]
        # automation cluster RESOLVED purely structurally (no seeds, no fallback): the insert
        # dispatched via the discovered path. (Read-back verification of the sentinel needs a
        # verification-complete reader, which is guaranteed only on the seed-first/cache path
        # exercised by test_probe_normal_resolves_and_verifies, not the blind stress path.)
        aw = caps["automation_write"]
        assert aw.get("via") == "discovered", f"blind automation not via discovery: {aw}"
        assert "inserted" in aw.get("detail", ""), f"blind automation did not insert: {aw}"
        # the descriptor reader SKELETON resolved structurally (keys exist and are non-empty).
        reader = report.get("reader") or {}
        for key in ("mX_", "ngq", "uEK"):
            assert reader.get(key), f"blind reader missing {key}: {reader}"
        # the clip + note were created (the command/clip path works under blind discovery).
        assert caps["clip_create"]["detail"].startswith("created clip"), (
            f"blind clip not created: {caps['clip_create']}"
        )

        # blind discovery intentionally does NOT write the symbol cache.
        cache = report.get("cache")
        assert cache is not None, "blind probe report missing cache block"
        assert cache.get("written") is False, f"blind probe must not cache: {cache}"
    finally:
        _cleanup_probe_tracks(b, before)


def test_audio_clip_insert_resolves_and_reads_back(live_bridge):
    """Arranger audio-clip insert resolves structurally and the file lands in the document.

    Audio insert is resolved structurally (ACIP) except the track-as-HrV accessor, which comes
    from data/cache. This drives the same path doctor validates: write a uniquely-named silent
    wav, insert it on a throwaway track, wait for the (async, off-thread) decode, then assert the
    descriptor walk surfaces the file name. A normal probe runs first so symbols are validated
    (doctor is mandatory; the gate would otherwise refuse non-diagnostic ops).
    """
    b = live_bridge
    idx, before = _make_probe_track(b)
    try:
        # validate symbols for this build (opens the gate; mirrors `openwig doctor`)
        b.request_op("resolver.probe", {"blind": False}, timeout=90)
        assert (b.request("resolver.result", timeout=10).get("report") or {}).get("ok") is True

        marker = "owtest_audio_%d" % os.getpid()
        wavp = Path(tempfile.gettempdir()) / (marker + ".wav")
        _write_silent_wav(wavp)
        try:
            b.request("track.insert_audio_clip", {"track": idx, "path": str(wavp)})
            time.sleep(2.5)  # decode is async / off-thread

            b.request_op("obj.walk", {"max_depth": 16, "max_nodes": 9000}, timeout=15)
            walk = b.request("obj.walk_result", timeout=10).get("json") or ""
            assert marker in walk, "inserted audio file did not surface in the descriptor walk"
        finally:
            try:
                wavp.unlink()
            except OSError:
                pass
    finally:
        _cleanup_probe_tracks(b, before)


def test_cache_roundtrip(live_bridge):
    """After a normal probe, resolver.status reflects a discovered/cached, matching cache."""
    b = live_bridge
    _, before = _make_probe_track(b)
    try:
        b.request_op("resolver.probe", {"blind": False}, timeout=90)
        res = b.request("resolver.result", timeout=10)
        report = res.get("report")
        assert report is not None, f"no probe report (error={res.get('error')!r})"
        assert report.get("ok") is True, f"probe not ok before status check: {report}"

        status = b.request("resolver.status", timeout=10)
        src = (status.get("symbol_source") or "").lower()
        assert "discover" in src or "cache" in src, f"unexpected symbol_source: {status}"
        assert status.get("cache_exists") is True, f"cache_exists false: {status}"
        assert status.get("cache_matches") is True, f"cache_matches false: {status}"

        reader = status.get("reader") or {}
        assert reader, "resolver.status reader dict is empty"
        assert all(reader.get(k) for k in ("mX_", "KRt", "bf", "ngq", "nI_", "Xzy", "uEK")), (
            f"resolver.status reader not fully populated: {reader}"
        )
    finally:
        _cleanup_probe_tracks(b, before)
