"""openwig.diagnostics - live self-test of the reflection paths (the "resolver").

The bridge reaches into Bitwig's obfuscated internals to write arranger automation,
create arranger clips, and read the document graph. Those internal class/method names are
stable for a given Bitwig build but get re-obfuscated each release, so they can move
between versions. `run_selftest` verifies, on a throwaway track, that each path still works
on the LIVE build and returns a capability report. `openwig doctor` prints it.

The probe creates a temporary instrument track named ``__openwig_probe__``, writes to it,
verifies the writes via a descriptor read, then deletes it. It never modifies your existing
tracks. It fails safe: a broken path is reported, not silently ignored.
"""
from __future__ import annotations

import time

from openwig.bridge import BridgeClient, BridgeError

PROBE_TRACK = "__openwig_probe__"


def _find_track_index(b, name):
    """Index of the track named ``name`` (by name, never by the flaky exists flag)."""
    snap = b.request("state.snapshot")
    for t in snap.get("tracks", []):
        if t.get("name") == name:
            return t.get("index")
    return None


def _delete_all_named(b, name, limit=6):
    """Delete every track named ``name`` (self-heals probe tracks left by a crashed run)."""
    for _ in range(limit):
        idx = _find_track_index(b, name)
        if idx is None:
            return
        try:
            b.request("track.delete", {"index": idx})
        except BridgeError:
            return


def run_selftest(b=None, *, timeout=15.0):
    """Run the resolver self-test against live Bitwig.

    Returns the report dict from ``resolver.probe`` augmented with a top-level
    ``connected`` flag (and ``classes`` / ``bitwig`` even when the round-trip can't run).
    Creates and deletes a temporary probe track; never modifies existing tracks.
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
        try:
            _delete_all_named(b, PROBE_TRACK)   # clear any probe track left by a prior crash
            b.request("track.create", {"type": "instrument", "name": PROBE_TRACK})
            # wait for the track to appear (createInstrumentTrack + a scheduled rename)
            idx = None
            for _ in range(8):
                time.sleep(0.25)
                idx = _find_track_index(b, PROBE_TRACK)
                if idx is not None:
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
            return report
        finally:
            # always remove the probe track(s), identifying BY NAME (never index alone)
            _delete_all_named(b, PROBE_TRACK)
    finally:
        if own:
            b.stop()
