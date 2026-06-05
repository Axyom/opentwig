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


def _read_cursor_all_pages(b):
    """All remote params of the CURRENT cursor device across every page, as a list of
    {page, index, name, value}. Selecting a page is async, so do it one at a time."""
    pages = b.request("device.all_remote_pages") or []
    npages = len(pages) if pages else 1
    out = []
    for pgi in range(npages):
        b.request("device.select_remote_page", {"page": pgi}); time.sleep(0.1)
        d = b.request("state.snapshot").get("device") or {}
        for r in d.get("remotes", []):
            if r.get("exists"):
                out.append({"page": pgi, "index": r.get("index"),
                            "name": r.get("name"), "value": r.get("value")})
    b.request("device.select_remote_page", {"page": 0}); time.sleep(0.05)
    return out


def _device_chain(b, track_idx, max_devices=12):
    """Walk a track's device chain via the cursor device. Each device record carries name,
    its page-0 remotes (`remotes`), and ALL remote params across pages (`all_remotes`)."""
    b.request("track.select", {"index": track_idx}); time.sleep(0.3)
    for _ in range(max_devices):                       # rewind cursor to the first device
        try: b.request("device.select_previous")
        except Exception: break
    time.sleep(0.35)                                   # let the first device's remote values settle
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
        all_remotes = _read_cursor_all_pages(b)
        chain.append({"name": name, "remotes": remotes, "all_remotes": all_remotes})
        last = name
        b.request("device.select_next"); time.sleep(0.3)    # let remote values update before next read
    return chain


def _diff_device_params(b, out, eps=0.006):
    """Fill each device's `params` with the remote values that DIFFER from the device's
    factory/preset default - so recreate restores only the parameters the user actually
    changed. Defaults are read by inserting a fresh copy on a throwaway track. Mutates
    `out` in place; best-effort (skips on any error, always removes the temp track)."""
    from openwig.recreate import _bitwig_dirs, _build_preset_index, _resolve_device
    from openwig.song import FACTORY
    factory_dir, preset_dirs = _bitwig_dirs()
    preset_idx = _build_preset_index(preset_dirs)

    b.request("track.create", {"type": "instrument", "name": "__openwig_baseline__", "index": -1})
    time.sleep(0.6)
    temp_idx = max((t.get("index", -1) for t in b.request("state.snapshot").get("tracks", [])), default=None)
    cache = {}
    try:
        for t in out.get("tracks", []):
            for d in t.get("devices", []):
                live = d.get("all_remotes") or []
                if not live:
                    continue
                kind, ref = _resolve_device(d.get("name", ""), factory_dir, preset_idx)
                if kind == "unknown":
                    continue
                sig = (kind, ref or d.get("name"))
                if sig not in cache:
                    cache[sig] = _read_baseline(b, temp_idx, kind, ref, d.get("name", ""), FACTORY)
                base = cache[sig]
                changed, seen = [], set()
                for r in live:
                    lv = r.get("value")
                    if not isinstance(lv, (int, float)):
                        continue
                    bv = base.get((r["page"], r["index"]))
                    if bv is None or abs(lv - bv) > eps:
                        # the same underlying parameter is often exposed on several pages;
                        # capture it once (same name + value) to avoid redundant restores
                        key = (r.get("name"), round(lv, 4))
                        if key in seen:
                            continue
                        seen.add(key)
                        changed.append({"page": r["page"], "index": r["index"],
                                        "name": r.get("name"), "value": lv})
                d["params"] = changed
    finally:
        if temp_idx is not None:
            try: b.request("track.delete", {"index": temp_idx}); time.sleep(0.3)
            except Exception: pass


def _read_baseline(b, temp_idx, kind, ref, name, factory_dir):
    """Insert a fresh copy of a device on the temp track, read its default all-page remote
    values, then remove it. Returns {(page, index): value}."""
    b.request("track.select", {"index": temp_idx}); time.sleep(0.3)
    if kind == "preset":
        b.request("device.insert_preset", {"path": ref}); time.sleep(1.3)
    else:
        b.request("device.insert_file", {"path": f"{factory_dir}/{name}.bwdevice"}); time.sleep(1.3)
    vals = {(r["page"], r["index"]): r.get("value") for r in _read_cursor_all_pages(b)}
    try: b.request("device.delete"); time.sleep(0.4)
    except Exception: pass
    return vals


def read_project(b, with_devices=True, with_clips=False, with_params=True):
    """Read the open Bitwig project into a structured dict (input for
    `openwig.recreate`). Stops the transport first - walking the graph during
    playback can crash the controller. with_params=True also captures, per device, the
    remote params that were changed from the device's factory/preset default (this
    briefly inserts fresh copies on a throwaway track to learn the defaults)."""
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
    try:
        out["master"] = {"devices": (b.request("master.devices") or {}).get("devices", [])}
    except Exception:  # noqa: BLE001
        out["master"] = {"devices": []}
    if with_devices and with_params:
        try:
            _diff_device_params(b, out)
        except Exception:  # noqa: BLE001 - param capture is best-effort
            pass
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
