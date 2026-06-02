#!/usr/bin/env python3
"""project_clear.py - clear the OPEN Bitwig project to a clean slate.

Deletes all non-master tracks and removes every device from the master FX chain (the
master track itself is kept). This is the "clean slate" the SDK demos previously needed a
Bitwig restart for.

  python tools/scripts/project_clear.py                # tracks + master FX
  python tools/scripts/project_clear.py --tracks-only
  python tools/scripts/project_clear.py --master-only

  from project_clear import clear_project
  clear_project(bridge)
"""
import sys
from openwig.bridge import BridgeClient


def clear_project(b, tracks=True, master=True):
    """Return {'tracks_deleted': n, 'master_devices_deleted': m}."""
    if tracks and master:
        r = b.request("project.clear")
        return {"tracks_deleted": r.get("tracks_deleted", 0),
                "master_devices_deleted": r.get("master_devices_deleted", 0)}
    out = {}
    if tracks:
        out["tracks_deleted"] = b.request("track.delete_all").get("deleted", 0)
    if master:
        out["master_devices_deleted"] = b.request("master.clear").get("deleted", 0)
    return out


def main():
    tracks = "--master-only" not in sys.argv
    master = "--tracks-only" not in sys.argv
    b = BridgeClient(request_timeout=20)
    b.start()
    if not b.wait_connected(6):
        print("NOT CONNECTED -- is Bitwig running with OpenwigBridge?")
        sys.exit(1)
    b.request("transport.stop")
    print("cleared:", clear_project(b, tracks, master))
    b.stop()


if __name__ == "__main__":
    main()
