"""openwig.wire - low-level wire-protocol helpers (advanced).

Most users want the top-level API (`Song`, `Track`). These modules expose the
raw building blocks the SDK uses internally:

    openwig.wire.automation    offline + recorded automation primitives
    openwig.wire.render        WASAPI-loopback render-to-wav

Stability is not guaranteed across SDK versions.
"""
from openwig.wire import automation, render  # noqa: F401
