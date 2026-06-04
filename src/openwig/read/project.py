"""openwig.read.project - read the OPEN Bitwig project live.

Reads structure (tempo, every track's name/volume/pan/mute/solo/arm + device
chain with remote values) via the controller, and - with with_clips=True - each
track's MIDI clips + arranger automation via the descriptor-graph walk. The
result dict is the input to `openwig.recreate.to_script`.

    from openwig.read import read_project
    data = read_project(bridge, with_clips=True)
"""
import json
import sys
import time
from pathlib import Path

from openwig.bridge import BridgeClient
from openwig.read.notes import read_track as _read_track_clips


def _device_chain(b, track_idx, max_devices=12):
    """Walk a track's device chain via the cursor device. Each device record
    carries name + the active remote-control parameters with their CURRENT VALUES."""
    b.request("track.select", {"index": track_idx}); time.sleep(0.2)
    for _ in range(max_devices):                       # rewind cursor to the first device
        try: b.request("device.select_previous")
        except Exception: break
    time.sleep(0.1)
    chain = []; last = None
    for _ in range(max_devices):
        d = b.request("state.snapshot").get("device") or {}
        if not d.get("exists"):
            break
        name = d.get("name")
        if name == last and chain:                     # cursor didn't advance -> end of chain
            break
        remotes = []
        for r in d.get("remotes", []):
            if not r.get("exists"):
                continue
            remotes.append({"index": r.get("index"), "name": r.get("name"),
                            "value": r.get("value"), "disp": r.get("disp")})
        chain.append({"name": name, "remotes": remotes})
        last = name
        b.request("device.select_next"); time.sleep(0.08)
    return chain


def read_project(b, with_devices=True, with_clips=False):
    """Read the open Bitwig project into a structured dict (input for
    `openwig.recreate`). Stops the transport first - walking the graph during
    playback can crash the controller."""
    b.request("transport.stop")
    snap = b.request("state.snapshot")
    tr = snap.get("transport", {})
    out = {
        "tempo": tr.get("tempo") or snap.get("tempo"),
        "transport": {k: tr.get(k) for k in ("playing", "loop", "metronome", "overdub", "position")},
        "tracks": [],
        "effect_tracks": [],
    }
    for t in snap.get("tracks", []):
        rec = {
            "index": t.get("index"), "name": t.get("name"),
            "volume": t.get("volume"), "pan": t.get("pan"),
            "volume_db": t.get("volume_db"), "pan_disp": t.get("pan_disp"),
            "mute": t.get("mute"), "solo": t.get("solo"), "arm": t.get("arm"),
        }
        if with_devices:
            rec["devices"] = _device_chain(b, t["index"])
        if with_clips:
            try:
                cd = _read_track_clips(b, t["index"])
                rec["clips"] = cd["clips"]
                rec["automation"] = cd["automation"]
            except Exception as e:  # noqa: BLE001
                rec["clips_error"] = str(e)
        out["tracks"].append(rec)
    for t in snap.get("effect_tracks", []):
        out["effect_tracks"].append({
            "index": t.get("index"), "name": t.get("name"),
            "volume": t.get("volume"), "pan": t.get("pan"),
            "mute": t.get("mute"), "solo": t.get("solo"),
        })
    return out


def summarize(d):
    lines = [f"PROJECT  tempo {d['tempo']} BPM"]
    for t in d["tracks"]:
        devs = " > ".join(dv["name"] for dv in t.get("devices", [])) or "(no devices)"
        flags = "".join(c for c, on in (("M", t["mute"]), ("S", t["solo"]), ("R", t["arm"])) if on)
        lines.append(f"  [{t['index']:>2}] {str(t['name']):<14} vol {str(t.get('volume_db')):>9}  "
                     f"pan {str(t['pan']):<7} {flags:<3} | {devs}")
        for ci, c in enumerate(t.get("clips", [])):
            lines.append(f"        clip[{ci}] @{c['clip_start']} len {c['clip_duration']}  {c['note_count']} notes")
        for a in t.get("automation", []):
            lines.append(f"        auto {a['param']}: {a['breakpoint_count']} bps")
    return "\n".join(lines)


if __name__ == "__main__":
    with_clips = "--clips" in sys.argv
    b = BridgeClient(request_timeout=20); b.start(); assert b.wait_connected(8), "bridge not connected"
    print("reading open project...")
    data = read_project(b, with_clips=with_clips)
    print(summarize(data))
    out = Path.cwd() / "project_read.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"\n-> {out}")
    b.stop()
