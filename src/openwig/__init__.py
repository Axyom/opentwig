"""openwig - algorithmic composition for Bitwig Studio.

Public API:
    from openwig import Song, Note
    s = Song(tempo=128, bars=4, clean=True)
    s.track("BASS", device="FM-4").clip([Note(33, b, vel=0.9) for b in range(16)])
    s.play(); s.render("out.wav")

A Note is `(key, start_beat, duration, velocity)` with named fields and defaults
(dur=0.5, vel=1.0); raw tuples work too. openwig deliberately stays a thin layer
over Bitwig and ships no note/curve/arrangement generators; bring your own.

Submodules:
    openwig.song              Song + Track (the composition API)
    openwig.bridge            low-level BridgeClient (advanced)
    openwig.wire              raw wire-protocol helpers (advanced)

Bitwig version compatibility: see pyproject.toml [tool.openwig]. The bridge
handshake refuses to connect on a version mismatch, because the SDK reaches
into Bitwig internals that move across releases.
"""
from __future__ import annotations

__version__ = "0.1.3"
# Bitwig's controller API host.getHostVersion() returns major.minor only
# (e.g. "6.0", not "6.0.6"), so we lock against that surface - there is no
# script-side way to distinguish 6.0.0 from 6.0.6. If a future point release
# breaks reflection on internals, we'll either patch or pin tighter via a
# separate runtime probe.
SUPPORTED_BITWIG_VERSIONS = frozenset({"6"})   # major versions accepted (6.0, 6.1, ...)

# ── high-level composition API ──────────────────────────────────────────────
from openwig.song import Song, Track, Note  # noqa: E402
from openwig.bridge import BridgeClient, BridgeError  # noqa: E402
from openwig.wire.render import render_to_wav  # noqa: E402

__all__ = [
    "__version__",
    "SUPPORTED_BITWIG_VERSIONS",
    "Song",
    "Track",
    "Note",
    "BridgeClient",
    "BridgeError",
    "render_to_wav",
]
