"""openwig - algorithmic composition for Bitwig Studio.

Public API:
    from openwig import Song, Track
    s = Song(tempo=128, bars=16, clean=True)
    s.track("BASS", device="FM-4").clip(s.pulse(33, step=1.0))
    s.play(); s.render("out.wav")

Submodules:
    openwig.song              Song + Track (top-level composition API)
    openwig.notes             note builders (pulse, walk, chord, hat)
    openwig.curves            envelope builders (ramp, lfo, expo, hold)
    openwig.arrangement       Section / genre templates
    openwig.export            render-section, stems, MIDI export, JSON round-trip
    openwig.lint              static checks on a Song before play/render
    openwig.live              live observers + helpers
    openwig.bridge            low-level BridgeClient (advanced)
    openwig.wire              raw wire-protocol helpers (advanced)

Bitwig version compatibility: see pyproject.toml [tool.openwig]. The bridge
handshake refuses to connect on a version mismatch, because the SDK reaches
into Bitwig internals that move across releases.
"""
from __future__ import annotations

__version__ = "0.1.0"
# Bitwig's controller API host.getHostVersion() returns major.minor only
# (e.g. "6.0", not "6.0.6"), so we lock against that surface - there is no
# script-side way to distinguish 6.0.0 from 6.0.6. If a future point release
# breaks reflection on internals, we'll either patch or pin tighter via a
# separate runtime probe.
SUPPORTED_BITWIG_VERSIONS = frozenset({"6"})   # major versions accepted (6.0, 6.1, ...)

# ── high-level composition API ──────────────────────────────────────────────
from openwig.song import Song, Track  # noqa: E402
from openwig.bridge import BridgeClient, BridgeError  # noqa: E402
from openwig.wire.render import render_to_wav  # noqa: E402

# ── helper submodules (importable as `from openwig import notes`) ────────
from openwig import notes, curves, arrangement, export, lint, live  # noqa: E402,F401

__all__ = [
    "__version__",
    "SUPPORTED_BITWIG_VERSIONS",
    "Song",
    "Track",
    "BridgeClient",
    "BridgeError",
    "render_to_wav",
    "notes",
    "curves",
    "arrangement",
    "export",
    "lint",
    "live",
]
