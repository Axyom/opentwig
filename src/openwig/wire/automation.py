"""wire_automation.py - offline arrangement automation via the bridge.

Despite the legacy filename ("wire_"), this module is bridge-only - it just
delegates to the `automation.write_offline` controller handler, which inserts
breakpoints directly into Bitwig's document via in-process reflection (no wire,
no recording, no playback). Kept under this name so existing imports work.
"""
import time


def write_offline(bridge, points, *, param: str = "volume", remote_index: int = 0):
    """Write arrangement-automation breakpoints DIRECTLY (no playback, no record).

    Select the target track first (the handler uses cursorTrack).

    points : [(beat, value0..1) | (beat, value, curvature) | (beat, value, curvature, "linear"|"hold")]
    param  : "volume" | "pan" | "remote"   (remote uses remote_index on the cursor device)

    Returns the bridge result; the insert runs async on the document thread. Sleeps
    0.5s before returning so successive calls don't overlap (GraalJS is single-threaded
    so two in-flight writes would collide).
    """
    res = bridge.request("automation.write_offline",
                         {"param": param, "remote_index": remote_index,
                          "points": [list(pt) for pt in points]})
    time.sleep(0.5)
    return res
