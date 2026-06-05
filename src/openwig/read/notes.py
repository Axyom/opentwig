"""openwig.read.notes - read MIDI notes + automation from arranger tracks, LIVE.

Uses the controller's generic in-process descriptor-graph reader (obj.walk) to
walk Bitwig's own document objects and pull real note/automation values - no wire
parsing. Arranger note clips live behind FvY-filtered relationships, so we walk
with no_filter and prune the device subtrees.

Object model (discovered in the bitwig-re research):
  main_note_clip_lane -> note_clip_event_timeline -> instrument_note_clip_event (a CLIP)
    -> ... -> instrument_note_event_timeline (one per KEY) -> instrument_note_event (a NOTE)
"""
import sys
import time
import json
from pathlib import Path

from openwig.bridge import BridgeClient

# Subtrees we never need (devices/aux/launcher) - pruned to keep the walk small
# AND to STOP it leaving the current track (track_mixer_module -> send_group ->
# track_group -> sibling track would otherwise pull in every OTHER track).
PRUNE = ["native_device", "device_contents", "nitro_atom", "polyphonic_note_voice_atom",
         "nested_device_chain", "remote_controls_page", "device_chain", "remote_control",
         "launcher_note_clip_slots", "launcher_automation_clip_slots", "modulation_source_atom",
         "track_mixer_module"]

CLIP_CLS = "instrument_note_clip_event"
KEY_TL_CLS = "instrument_note_event_timeline"
NOTE_CLS = "instrument_note_event"
LANE_CLS = ("permanent_automation_lane", "about_to_be_created_automation_lane")
BREAKPOINT_CLS = "decimal_value_event"

# prop ids (notes)
P_TIME, P_DUR, P_VON, P_VOFF = "687", "38", "239", "240"
P_KEY, P_CHAN = "238", "9857"
P_CLIP_NAME = "2958"
# prop ids (automation breakpoint = decimal_value_event)
P_BP_TIME, P_BP_VALUE, P_BP_INTERP = "687", "655", "13726"

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(k):
    try:
        k = int(k)
        return f"{NAMES[k % 12]}{k // 12 - 2}"
    except Exception:
        return "?"


def walk_track(bridge, idx, max_depth=16, max_nodes=9000):
    bridge.request("track.select", {"index": idx})
    time.sleep(0.8)            # let cursorTrack follow the selection before walking
    bridge.request("obj.walk", {"max_depth": max_depth, "max_nodes": max_nodes,
                                "no_filter": True, "prune": PRUNE,
                                "no_dedup": ["device_atom_reference"]})
    time.sleep(1.0)
    r = None
    for _ in range(60):
        r = bridge.request("obj.walk_result")
        if r and r.get("ready"):
            break
        time.sleep(0.3)
    if not r or not r.get("ready"):
        raise RuntimeError("walk timed out")
    if r.get("error"):
        raise RuntimeError(r["error"])
    return json.loads(r["json"])


def _num(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def collect_notes(node, cur_key=None, cur_chan=0, out=None):
    """Recurse the walk tree; emit a note dict per instrument_note_event,
    inheriting key/channel from the nearest enclosing key-timeline."""
    if out is None:
        out = []
    if not isinstance(node, dict):
        return out
    cls = node.get("_cls")
    if cls == KEY_TL_CLS:
        cur_key = node.get(P_KEY, cur_key)
        cur_chan = node.get(P_CHAN, cur_chan)
    if cls == NOTE_CLS:
        out.append({
            "key": int(_num(cur_key, -1)),
            "name": note_name(cur_key),
            "channel": int(_num(cur_chan, 0)),
            "start": round(_num(node.get(P_TIME)), 6),
            "duration": round(_num(node.get(P_DUR)), 6),
            "velocity": round(_num(node.get(P_VON)), 4),
            "release_velocity": round(_num(node.get(P_VOFF)), 4),
        })
    for v in node.values():
        if isinstance(v, list):
            for it in v:
                collect_notes(it, cur_key, cur_chan, out)
    return out


def collect_clips(node, out=None):
    """Find every note CLIP (instrument_note_clip_event) and the notes inside it."""
    if out is None:
        out = []
    if not isinstance(node, dict):
        return out
    if node.get("_cls") == CLIP_CLS:
        notes = collect_notes(node)
        notes.sort(key=lambda n: (n["start"], n["key"]))
        out.append({
            "clip_start": round(_num(node.get(P_TIME)), 6),
            "clip_duration": round(_num(node.get(P_DUR)), 6),
            "name": node.get(P_CLIP_NAME, ""),
            "note_count": len(notes),
            "notes": notes,
        })
        return out  # don't double-descend into nested clip refs
    for v in node.values():
        if isinstance(v, list):
            for it in v:
                collect_clips(it, out)
    return out


def _param_name(node):
    """Best-effort name of the parameter an automation lane targets: the nearest
    value-atom class found under the lane, plus any title string on it."""
    found = [None]

    def rec(o):
        if found[0] or not isinstance(o, dict):
            return
        cls = o.get("_cls", "")
        if cls.endswith("_value_atom") or cls.endswith("_atom"):
            title = o.get("347") or o.get("2958") or ""
            found[0] = f"{cls}" + (f" ({title})" if title else "")
            return
        for v in o.values():
            if isinstance(v, list):
                for it in v:
                    rec(it)
    rec(node)
    return found[0] or "?"


def collect_breakpoints(node, out=None):
    if out is None:
        out = []
    if not isinstance(node, dict):
        return out
    if node.get("_cls") == BREAKPOINT_CLS:
        out.append({
            "time": round(_num(node.get(P_BP_TIME)), 6),
            "value": round(_num(node.get(P_BP_VALUE)), 6),
            "interp": node.get(P_BP_INTERP, "LINEAR"),
        })
    for v in node.values():
        if isinstance(v, list):
            for it in v:
                collect_breakpoints(it, out)
    return out


def _ids_under(node, out):
    """All integer-ish ids (object _id + numeric props) under `node`."""
    if not isinstance(node, dict):
        return
    for k, v in node.items():
        if isinstance(v, int):
            out.add(v)
        elif isinstance(v, str) and v.lstrip("-").isdigit():
            out.add(int(v))
        elif isinstance(v, list):
            for it in v:
                _ids_under(it, out)


def _ref_ids(lane):
    """Candidate target-object ids found under the lane's device_atom_reference(s)."""
    out = set()

    def rec(n):
        if not isinstance(n, dict):
            return
        for v in n.values():
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and it.get("_cls") == "device_atom_reference":
                        _ids_under(it, out)
                    rec(it)
    rec(lane)
    return out


def collect_automation(node, out=None):
    """Find automation lanes that actually carry breakpoints."""
    if out is None:
        out = []
    if not isinstance(node, dict):
        return out
    if node.get("_cls") in LANE_CLS:
        bps = collect_breakpoints(node)
        if bps:
            bps.sort(key=lambda b: b["time"])
            out.append({"param": _param_name(node), "breakpoint_count": len(bps),
                        "breakpoints": bps, "ref_ids": sorted(_ref_ids(node))})
        return out  # a lane's subtree is self-contained
    for v in node.values():
        if isinstance(v, list):
            for it in v:
                collect_automation(it, out)
    return out


def read_device_atom_map(bridge, idx, max_devices=16):
    """Map each device-remote-param's document-atom id -> (device_index, remote_index,
    device_name, param_name) for the track's device chain. Navigates the cursor device
    (page 0 of each device's remote controls)."""
    bridge.request("track.select", {"index": idx}); time.sleep(0.3)
    for _ in range(max_devices):
        try: bridge.request("device.select_previous")
        except Exception: break
    time.sleep(0.2)
    amap = {}; last = None
    for di in range(max_devices):
        d = bridge.request("state.snapshot").get("device") or {}
        if not d.get("exists"):
            break
        nm = d.get("name")
        if nm == last and amap:
            break
        rr = bridge.request("device.remote_atom_ids") or {}
        for pm in rr.get("params", []):
            for aid in pm.get("atom_ids", []):
                amap.setdefault(aid, (di, pm.get("remote_index"), nm, pm.get("name", "")))
        last = nm
        bridge.request("device.select_next"); time.sleep(0.1)
    return amap


def read_remote_calibration(bridge, idx, targets, settle=0.5):
    """Measure the affine native<->normalized map for the given remote targets.
    Breakpoint values are stored in a param's RAW/native units; automate() wants 0..1.
    The map is affine (verified), so set each param to normalized 0 and 1, read getRaw
    at each, and (off=raw@0, scale=raw@1 - raw@0) inverts it: norm = (raw - off) / scale.

    `targets`: {device_index -> set(remote_index)}.
    Returns {(device_index, remote_index): (off, scale)}. Non-destructive: restores
    each param to its original value afterward.
    """
    out = {}
    bridge.request("track.select", {"index": idx}); time.sleep(0.2)

    def raw_now():
        return {pm["index"]: pm.get("raw")
                for pm in (bridge.request("device.remote_raw") or {}).get("params", [])}

    for di in sorted(targets):
        ris = sorted(targets[di])
        bridge.request("device.select_index", {"index": int(di)}); time.sleep(0.5)
        snap = bridge.request("state.snapshot").get("device") or {}
        orig = {r["index"]: r.get("value") for r in snap.get("remotes", []) if r.get("exists")}
        for ri in ris: bridge.request("device.set_remote", {"index": ri, "value": 0.0})
        time.sleep(settle); raw0 = raw_now()
        for ri in ris: bridge.request("device.set_remote", {"index": ri, "value": 1.0})
        time.sleep(settle); raw1 = raw_now()
        for ri in ris:                                     # restore originals
            if isinstance(orig.get(ri), (int, float)):
                bridge.request("device.set_remote", {"index": ri, "value": orig[ri]})
        for ri in ris:
            o, s1 = raw0.get(ri), raw1.get(ri)
            if isinstance(o, (int, float)) and isinstance(s1, (int, float)) and abs(s1 - o) > 1e-12:
                out[(di, ri)] = (o, s1 - o)
    return out


def read_track(bridge, idx):
    tree = walk_track(bridge, idx)
    clips = collect_clips(tree)
    autos = collect_automation(tree)
    # resolve each lane's target: volume / pan by class, device params by id-matching
    needs_map = any("volume" not in (a.get("param") or "").lower()
                    and "pan" not in (a.get("param") or "").lower() for a in autos)
    amap = read_device_atom_map(bridge, idx) if needs_map else {}
    for a in autos:
        param = (a.get("param") or "").lower()
        if "volume" in param:
            a["target"] = {"kind": "volume"}
        elif "pan" in param:
            a["target"] = {"kind": "pan"}
        else:
            hit = next((amap[r] for r in a.get("ref_ids", []) if r in amap), None)
            a["target"] = ({"kind": "remote", "device_index": hit[0], "remote_index": hit[1],
                            "device": hit[2], "param": hit[3]} if hit else {"kind": "unknown"})
        a.pop("ref_ids", None)
    # calibrate native->normalized for the resolved remote targets (the walk reads
    # breakpoints in raw units; automate() needs 0..1)
    rt = {}
    for a in autos:
        tg = a.get("target") or {}
        if tg.get("kind") == "remote" and tg.get("remote_index") is not None:
            rt.setdefault(tg["device_index"], set()).add(tg["remote_index"])
    if rt:
        calib = read_remote_calibration(bridge, idx, rt)
        for a in autos:
            tg = a.get("target") or {}
            c = calib.get((tg.get("device_index"), tg.get("remote_index"))) if tg.get("kind") == "remote" else None
            if c:
                tg["value_off"], tg["value_scale"] = c
    return {"clips": clips, "automation": autos}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    idxs = [int(a) for a in args] if args else list(range(10))
    b = BridgeClient(request_timeout=15.0)
    b.start()
    if not b.wait_connected(5.0):
        print("NOT CONNECTED -- is Bitwig running with OpenwigBridge?")
        sys.exit(1)
    b.request("transport.stop")
    for idx in idxs:
        try:
            data = read_track(b, idx)
        except Exception as e:  # noqa: BLE001
            print(f"track {idx}: ERROR {e}")
            continue
        clips, autos = data["clips"], data["automation"]
        total = sum(c["note_count"] for c in clips)
        if not clips and not autos:
            print(f"track {idx}: (no note clips / automation)")
            continue
        print(f"track {idx}: {len(clips)} clip(s), {total} note(s), {len(autos)} automation lane(s)")
    b.stop()


if __name__ == "__main__":
    main()
