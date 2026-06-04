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
                                "no_filter": True, "prune": PRUNE})
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
                        "breakpoints": bps})
        return out  # a lane's subtree is self-contained
    for v in node.values():
        if isinstance(v, list):
            for it in v:
                collect_automation(it, out)
    return out


def read_track(bridge, idx):
    tree = walk_track(bridge, idx)
    return {"clips": collect_clips(tree), "automation": collect_automation(tree)}


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
