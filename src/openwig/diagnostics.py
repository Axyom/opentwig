"""openwig.diagnostics - live self-test of the reflection paths (the "resolver").

The bridge reaches into Bitwig's obfuscated internals to write arranger automation,
create arranger clips, and read the document graph. Those internal class/method names are
stable for a given Bitwig build but get re-obfuscated each release, so they can move
between versions. `run_selftest` verifies, on a throwaway track, that each path still works
on the LIVE build, resolves the descriptor-reader names, and (controller side) writes a
symbol cache the bridge loads at init. `openwig doctor` prints the report.

The probe creates a temporary instrument track, writes to it, verifies the writes via a
descriptor read, then deletes it. It never modifies your existing tracks. It fails safe: a
broken path is reported, not silently ignored.
"""
from __future__ import annotations

import os
import struct
import sys
import time
import wave
from pathlib import Path

from openwig.bridge import BridgeClient, BridgeError

PROBE_TRACK = "__openwig_probe__"


def _data_dir() -> Path:
    """openwig data dir, matching the controller's (where the cache + log live)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "openwig"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "openwig"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "openwig"


def _write_silent_wav(path: Path, seconds: float = 0.1):
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(44100 * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(struct.pack("<" + "h" * n, *([0] * n)))


def _validate_audio(b, idx):
    """Validate (and if needed re-resolve) arranger audio-clip insert on track `idx`.

    The dispatch / ZjS / mode resolve structurally controller-side; only the track-as-HrV
    accessor needs execution validation (audio insert is async, so this is orchestrated here:
    insert a uniquely-named test wav via a candidate accessor, wait for the decode, and check
    the descriptor walk surfaces the file name). Returns {ok, detail, hrv}.
    """
    res = b.request("resolver.audio_candidates")
    if res.get("error"):
        return {"ok": False, "detail": res["error"]}
    cands = res.get("hrv_candidates") or []
    data = _data_dir()

    def walk_text():
        b.request_op("obj.walk", {"max_depth": 16, "max_nodes": 9000}, timeout=12.0)
        return b.request("obj.walk_result").get("json") or ""

    # try the current default (None override) first, then each candidate
    for hrv in [None] + cands:
        marker = "owtest_" + (hrv or "default")
        wavp = data / (marker + ".wav")
        try:
            _write_silent_wav(wavp)
        except OSError as exc:
            return {"ok": False, "detail": f"cannot write test wav: {exc}"}
        params = {"track": idx, "path": str(wavp)}
        if hrv:
            params["hrv"] = hrv
        b.request("track.insert_audio_clip", params)
        time.sleep(2.5)  # audio decode is async/off-thread
        if marker in walk_text():
            if hrv:
                b.request("resolver.set_audio_hrv", {"hrv": hrv})
            return {"ok": True, "hrv": hrv or "(default)", "detail": "inserted + read back"}
    return {"ok": False, "detail": "no HrV accessor produced a clip"}


def _occupied(b):
    """Set of main-track indices currently occupied (by name; the exists flag is flaky)."""
    snap = b.request("state.snapshot")
    return {t.get("index") for t in snap.get("tracks", []) if t.get("name")}


def _delete_index(b, idx):
    try:
        b.request("track.delete", {"index": idx})
    except BridgeError:
        pass


def run_selftest(b=None, *, timeout=90.0):
    """Run the resolver self-test against live Bitwig.

    Returns the report dict from ``resolver.probe`` augmented with a top-level
    ``connected`` flag (and ``classes`` / ``bitwig`` even when the round-trip can't run).
    Creates and deletes a temporary probe track; never modifies existing tracks. The probe
    track is identified by INDEX-DIFF (the new slot that appears), not by name, because the
    post-create rename can silently fail right after a controller reload.
    """
    own = b is None
    if own:
        b = BridgeClient()
        b.start()
    try:
        if not b.wait_connected(4.0):
            return {"connected": False}
        # class-load check is cheap and works even if the round-trip can't run
        classes = b.request("resolver.classes")
        base = {"connected": True,
                "classes": classes.get("classes"),
                "bitwig": classes.get("bitwig")}
        before = _occupied(b)
        new_indices = []
        try:
            b.request("track.create", {"type": "instrument", "name": PROBE_TRACK})
            # wait for a NEW occupied slot to appear (rename may fail, so do not match by name)
            idx = None
            for _ in range(8):
                time.sleep(0.25)
                new_indices = sorted(_occupied(b) - before)
                if new_indices:
                    idx = new_indices[-1]
                    break
            if idx is None:
                base["error"] = "probe track did not appear (cannot run round-trip)"
                return base
            b.request("track.select", {"index": idx})
            time.sleep(0.3)
            b.request_op("resolver.probe", timeout=timeout)
            res = b.request("resolver.result")
            report = res.get("report") or dict(base)
            report["connected"] = True
            if res.get("error"):
                report["error"] = res["error"]
            # arranger audio-clip insert is validated separately (async; orchestrated here)
            try:
                report["audio"] = _validate_audio(b, idx)
            except BridgeError as exc:
                report["audio"] = {"ok": False, "detail": f"error ({exc})"}
            return report
        finally:
            # remove every slot that appeared during the probe (covers a failed rename),
            # deleting from the highest index down so earlier indices stay valid.
            for idx in sorted(_occupied(b) - before, reverse=True):
                _delete_index(b, idx)
    finally:
        if own:
            b.stop()
