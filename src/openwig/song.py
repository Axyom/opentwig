"""openwig.song - declarative SDK for composing Bitwig songs.

Daemon-free: everything goes through the OpenwigBridge controller (TCP :7777). No
mitm_daemon, no admin rights, no port 7880. Clips + notes are created via the
new `clip.create_arranger_with_notes` bridge handler, which calls Bitwig's own
GUI commands (insert_instrument_clip_on_arranger + insert_note) in-process.

Consolidates the whole toolkit into Track/Song objects:
  * devices            (factory device by file path, or a Bitwig device by uuid)
  * clips + notes      (controller: insert_instrument_clip_on_arranger + insert_note)
  * automation         (offline, controller internal-access path: volume / pan / device remote)
  * mix + master chain + render to .wav (loopback capture)

Notes are `Note(key, start, dur, vel)` tuples (or plain tuples) - build them
with ordinary Python.

Example:
    from openwig import Song, Note
    s = Song(tempo=128, bars=4)
    s.track("KICK", device="v9 Kick").clip([Note(36, b, dur=0.25) for b in range(16)])
    (s.track("BASS", device="FM-4").fx("Filter").fx("Saturator", Drive=0.25)
       .clip([Note(33, b + 0.5, dur=0.4, vel=0.85) for b in range(16)]))
    s.master(["EQ+", "Compressor+", "Peak Limiter"])
    s.play(); print(s.render("song.wav"))
"""
import os
import time
import warnings
from typing import NamedTuple
from openwig.wire import automation as wa
from openwig.wire.render import render_to_wav
from openwig.bridge import BridgeClient, BridgeError


class Note(NamedTuple):
    """A single note. A plain tuple with named fields and defaults, so you can
    write `Note(36, beat)` instead of `(36, beat, 0.5, 1.0)`. Interchangeable
    with raw `(key, start, dur, vel[, channel])` tuples everywhere openwig takes
    notes - `clip()`, `clips()`, `scene()` all accept either."""
    key: int          # MIDI note number (0-127)
    start: float      # start position in beats, relative to the clip
    dur: float = 0.5  # duration in beats
    vel: float = 1.0  # velocity, 0.0-1.0
    channel: int = 0  # MIDI channel


def _find_bitwig_root():
    env = os.environ.get("BITWIG_PATH")
    if env:
        return env.replace("\\", "/").rstrip("/")
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ):
                try:
                    with winreg.OpenKey(hive, sub) as uk:
                        for i in range(winreg.QueryInfoKey(uk)[0]):
                            try:
                                with winreg.OpenKey(uk, winreg.EnumKey(uk, i)) as k:
                                    name = winreg.QueryValueEx(k, "DisplayName")[0]
                                    if "Bitwig" in name:
                                        loc = winreg.QueryValueEx(k, "InstallLocation")[0]
                                        if loc:
                                            return loc.replace("\\", "/").rstrip("/")
                            except OSError:
                                continue
                except OSError:
                    continue
    except ImportError:
        pass
    return "C:/Program Files/Bitwig Studio"


_BITWIG_ROOT = _find_bitwig_root()
FACTORY   = f"{_BITWIG_ROOT}/Library/devices"
HOLD = lambda beat, val: (beat, val, 0.0, "hold")


class Track:
    def __init__(self, song, name, device=None, uuid=None, kind="instrument"):
        """kind: 'instrument' | 'audio' | 'effect' (return track). Effect tracks live in
        a separate bank - addressed via fxtrack.* handlers + state.snapshot['effect_tracks']."""
        self.s = song; self.name = name; self.kind = kind
        self.bank = "effect" if kind == "effect" else "main"
        self._snap_key = "effect_tracks" if self.bank == "effect" else "tracks"
        self._clip_specs = []        # [(start, dur, notes), ...] -- for export_midi / to_dict
        self._fx_spec = []           # [{"name": ..., "remotes": {...}}, ...]
        self._auto_spec = []         # [{"param": ..., "points": [...], "remote_index": ...}]
        self._device_name = None; self._device_uuid = None
        # delete any pre-existing tracks with this name (in the right bank)
        for t in sorted([t for t in song.b.request("state.snapshot").get(self._snap_key, []) if t["name"] == name], key=lambda t:-t["index"]):
            song.b.request(f"{self._del_method()}", {"index": t["index"]}); time.sleep(0.2)
        song.b.request("track.create", {"type": kind, "name": name, "index": -1})
        self.idx = None
        for _ in range(20):
            time.sleep(0.3)
            self.idx = next((t["index"] for t in song.b.request("state.snapshot").get(self._snap_key, []) if t["name"] == name), None)
            if self.idx is not None: break
        if self.idx is None:
            raise RuntimeError(f"track '{name}' (bank={self.bank}) not visible in snapshot after create")
        self.select()
        if device: self.add_device(device)
        elif uuid: self.add_bitwig(uuid)

    # ── bank-aware method names ──────────────────────────────────────────────
    def _del_method(self):       return "fxtrack.delete" if self.bank == "effect" else "track.delete"
    def _ns(self, method):
        """Pick the right handler namespace ('track.X' or 'fxtrack.X') by bank."""
        return f"fxtrack.{method}" if self.bank == "effect" else f"track.{method}"

    def select(self):
        self.s.b.request(self._ns("select"), {"index": self.idx}); time.sleep(0.25); return self

    # ── channel-strip controls (all chainable; bank-aware) ───────────────────
    def pan(self, value):
        """Set pan. Accepts -1.0 (full left) .. 0.0 (center) .. +1.0 (full right)-
        the convention Bitwig's GUI shows. Internally normalised to the controller's
        0..1 range (0 = full left, 0.5 = center, 1 = full right)."""
        v = max(-1.0, min(1.0, float(value)))
        normalised = (v + 1.0) / 2.0
        self.s.b.request(self._ns("set_pan"), {"index": self.idx, "value": normalised})
        time.sleep(0.03); return self

    def mute(self, on=True):
        self.s.b.request(self._ns("set_mute"), {"index": self.idx, "on": bool(on)})
        time.sleep(0.03); return self

    def solo(self, on=True):
        self.s.b.request(self._ns("set_solo"), {"index": self.idx, "on": bool(on)})
        time.sleep(0.03); return self

    def arm(self, on=True):
        """Main-bank only; effect tracks aren't armable."""
        if self.bank == "effect":
            warnings.warn(f"arm() ignored: '{self.name}' is an effect track (not armable)", stacklevel=2)
            return self
        self.s.b.request("track.set_arm", {"index": self.idx, "on": bool(on)})
        time.sleep(0.03); return self

    def monitor(self, mode="AUTO"):
        """Main-bank only; mode: 'OFF' | 'AUTO' | 'ON'."""
        if self.bank == "effect":
            warnings.warn(f"monitor() ignored: '{self.name}' is an effect track", stacklevel=2)
            return self
        self.s.b.request("track.set_monitor", {"index": self.idx, "mode": mode})
        time.sleep(0.03); return self

    def color(self, r, g, b, a=1.0):
        self.s.b.request(self._ns("set_color"),
                         {"index": self.idx, "r": float(r), "g": float(g), "b": float(b), "a": float(a)})
        time.sleep(0.03); return self

    def send(self, send_index, value):
        """Set a send level (send_index 0..NUM_SENDS-1) - main-bank only.
        Effect tracks RECEIVE sends; they don't send themselves."""
        if self.bank == "effect":
            warnings.warn(f"send() ignored: '{self.name}' is an effect track (effect tracks receive sends, they don't send)", stacklevel=2)
            return self
        self.s.b.request("track.set_send",
                         {"index": self.idx, "send": int(send_index), "value": float(value)})
        time.sleep(0.03); return self

    def rename(self, name):
        self.s.b.request(self._ns("rename"), {"index": self.idx, "name": name})
        self.name = name; time.sleep(0.05); return self

    # ── devices / fx ──────────────────────────────────────────────────────────
    def add_device(self, name):
        self.select()
        self.s.b.request_insert("device.insert_file", {"path": FACTORY + "/" + name + ".bwdevice"}, fallback=1.0)
        if self._device_name is None: self._device_name = name
        return self

    def add_bitwig(self, uuid):
        self.select()
        self.s.b.request_insert("device.insert_bitwig", {"uuid": uuid}, fallback=1.2)
        if self._device_uuid is None: self._device_uuid = uuid
        return self

    def fx(self, name, **remotes):
        """Insert an FX device; tune named remotes (case-insensitive substring), e.g.
        .fx("Saturator", Drive=0.25) / .fx("Reverb", Mix=0.3)."""
        self.add_device(name)
        for nm, val in remotes.items():
            self._set_remote(nm, val)
        self._fx_spec.append({"name": name, "remotes": dict(remotes)})
        return self

    def set_remotes(self, **remotes):
        """Set named remote-control values on the CURRENTLY-selected device (without
        inserting a device). Names match ignoring spaces/dashes, like fx()."""
        self.select()
        for nm, val in remotes.items():
            self._set_remote(nm, val)
        return self

    def _set_remote(self, sub, val):
        rem = (self.s.b.request("state.snapshot").get("device") or {}).get("remotes", [])
        # normalize both sides (drop spaces/dashes/dots) so e.g. "Pre_delay"
        # matches the remote "Pre-delay" and "R_Time" matches "R. Time".
        norm = lambda x: "".join(c for c in str(x).lower() if c.isalnum())
        nsub = norm(sub)
        for r in rem:
            if r.get("exists") and nsub in norm(r.get("name")):
                self.s.b.request("device.set_remote", {"index": r["index"], "value": val}); return r["name"]
        names = [r.get("name") for r in rem if r.get("exists")]
        warnings.warn(f"no remote parameter matching {sub!r} on this device; ignored. "
                      f"Available on the active page: {names}", stacklevel=3)
        return None

    # ── clips / notes ─────────────────────────────────────────────────────────
    def _make_clip(self, start, dur, notes):
        """Create ONE arranger clip at `start` (beats), length `dur`, filled with `notes`
        (each note's start_beat is RELATIVE to the clip start). Daemon-free: dispatches
        via the bridge handler `clip.create_arranger_with_notes`, which is fire-and-forget
        on the document-edit thread - we sleep proportional to the note count to let it
        commit before the next call queues another task."""
        self.select()
        payload_notes = []
        for nt in notes:
            if len(nt) < 4:
                raise ValueError(
                    f"each note needs at least (key, start, dur, vel); got {nt!r}. "
                    f"Use Note(key, start, dur=..., vel=...) or a 4+ element tuple.")
            key, st, du, vel = nt[0], nt[1], nt[2], nt[3]
            ch = nt[4] if len(nt) > 4 else 0
            payload_notes.append([int(ch), int(key), float(st), float(du), float(vel)])
        self.s.b.request_op("clip.create_arranger_with_notes", {
            "start": float(start), "duration": float(dur), "notes": payload_notes,
        }, fallback=0.5 + 0.005 * len(payload_notes), floor=0.2, timeout=15.0)
        # remember for later (.mid export, save_json, etc.)
        self._clip_specs.append((float(start), float(dur), [tuple(nt) for nt in notes]))

    def clip(self, notes, dur=None, start=0.0):
        """Create one arranger clip (default: spanning the song from beat 0) + fill it.
        notes: iterable of (key, start_beat, dur_beats, velocity_0_1[, channel])."""
        self._make_clip(start, self.s.total if dur is None else dur, notes)
        return self

    def clips(self, segments):
        """Create SEVERAL arranger clips on this track (gaps = the beats you leave between
        them). segments: [(start_beat, duration_beats, notes), ...]; notes are RELATIVE to
        each clip's start."""
        for start, dur, notes in segments:
            self._make_clip(start, dur, notes)
        return self

    def scene(self, slot, notes, *, dur=4.0, step_size=0.25):
        """Create a LAUNCHER clip at `slot` filled with `notes` (step-grid sequenced
        via clip.set_step). slot 0..NUM_CLIPS-1. notes: (key, start_beat, dur, vel)."""
        self.select()
        self.s.b.request("track.create_clip", {"track": self.idx, "slot": int(slot)})
        time.sleep(0.4)
        self.s.b.request("track.select_slot", {"track": self.idx, "slot": int(slot)})
        time.sleep(0.2)
        self.s.b.request("clip.select_launcher")
        self.s.b.request("clip.set_loop", {"on": True, "start": 0.0, "length": float(dur)})
        self.s.b.request("clip.set_step_size", {"size": float(step_size)})
        for nt in notes:
            key = int(nt[0]); st = float(nt[1]); du = float(nt[2]); vel = int(127 * float(nt[3]))
            x = int(round(st / step_size))
            self.s.b.request("clip.set_step",
                             {"x": x, "y": key, "velocity": vel, "duration": float(du)})
            time.sleep(0.005)
        return self

    def launch(self, slot=0):
        self.s.b.request("slot.launch", {"track": self.idx, "slot": int(slot)})
        return self

    def sample(self, path, slot=0):
        """Load a .wav/.aiff into a launcher slot on this (audio) track."""
        self.s.b.request("slot.insert_audio_file",
                         {"track": self.idx, "slot": int(slot), "path": str(path)})
        time.sleep(0.5); return self

    def audio_clip(self, path, start=0.0, duration=4.0):
        """Drop a `.wav`/`.aiff` onto the arranger at `start` (beats) with `duration` (beats).

        Uses the arranger insertion point resolved by `openwig doctor` (run it once per Bitwig
        build). Sleeps 1.5s after the call: Bitwig decodes the file off-thread and back-to-back
        inserts can saturate the controller queue."""
        self.s.b.request("track.insert_audio_clip", {
            "track": self.idx, "path": str(path),
            "start": float(start), "duration": float(duration),
        })
        time.sleep(1.5); return self

    def audio_clips(self, segments):
        """Lay several audio clips: `[(path, start, duration), ...]`."""
        for (path, start, dur) in segments:
            self.audio_clip(path, start=start, duration=dur)
        return self

    def describe_clip(self):
        """Enumerate every descriptor of the currently-selected clip
        (property IDs + current values). Used to discover stretch / loop / etc.
        property IDs at runtime - once known, they can be set via set_clip_prop."""
        self.select()
        return self.s.b.request("clip.describe", {"depth": 1})

    def set_clip_prop(self, prop_id, value):
        """Set a descriptor on the currently-selected clip by property ID.

        Discovery-oriented: use describe_clip() once to find the property IDs
        that hold stretch mode / factor on audio clips, then wire stretch()
        on top. Property IDs are stable across sessions but may differ across
        Bitwig versions."""
        self.select()
        return self.s.b.request("clip.set_prop", {"prop_id": str(prop_id), "value": value})

    def preset(self, path):
        """Load a .bwpreset file onto this track (replaces device chain)."""
        self.select()
        self.s.b.request_insert("device.insert_preset", {"path": str(path)}, fallback=1.0)
        return self

    def step_attr(self, x, key, attr, value):
        """Set a NoteStep attribute on the currently-selected clip's note at (x, key).
        attr: 'duration' | 'velocity' | 'release' | 'chance' | 'pressure' | 'timbre'
              | 'pan' | 'transpose' | 'gain'. value: numeric (0..1 for most)."""
        self.select()
        self.s.b.request("clip.set_step_attr",
                         {"x": int(x), "y": int(key), "attr": attr, "value": float(value)})
        return self

    def delete_device(self):
        """Delete the currently-selected device (cursorDevice) on this track."""
        self.select()
        self.s.b.request("device.delete"); time.sleep(0.3); return self

    def select_device(self, index):
        """Select the Nth device in the chain (0 = first)."""
        self.select()
        self.s.b.request("device.select_index", {"index": int(index)}); time.sleep(0.2); return self

    def remote_pages(self):
        """List ALL remote pages of the cursor device (not just active)."""
        self.select()
        return self.s.b.request("device.all_remote_pages")

    def select_remote_page(self, page):
        """Select the Nth remote-controls page of the cursor device (0 = first).
        Select the device first; `remote_index` in automate()/set_remotes refers to
        the page selected here."""
        self.s.b.request("device.select_remote_page", {"page": int(page)}); time.sleep(0.15); return self

    def set_remote_values(self, page, values):
        """Set remote params BY INDEX on a specific page of the CURRENTLY-selected device.
        `values`: {remote_index: 0..1}. Used by recreate to restore tweaked device
        parameters (select the device first)."""
        self.s.b.request("device.select_remote_page", {"page": int(page)}); time.sleep(0.12)
        for i, v in values.items():
            self.s.b.request("device.set_remote", {"index": int(i), "value": float(v)}); time.sleep(0.03)
        return self

    def routing_info(self):
        """Read this track's routing state (input source flags). READ-ONLY - Bitwig's
        API + cxu_2 schema have no setters for input/output/sidechain routing today
        (SourceSelector is read-only; only `find_first_sidechain_source_command` exists
        as a getter). To CHANGE routing, use the Bitwig GUI."""
        if self.bank == "effect": return {"note": "effect tracks have fixed routing"}
        return self.s.b.request("track.routing_info", {"index": self.idx})

    def transpose_cursor(self, semitones):
        """Transpose this track's selected clip by N semitones."""
        self.select()
        self.s.b.request("clip.transpose", {"semitones": int(semitones)}); return self

    def quantize_cursor(self, amount=1.0):
        """Quantize this track's selected clip's notes (0..1)."""
        self.select()
        self.s.b.request("clip.quantize", {"amount": float(amount)}); return self

    # ── mix / automation ──────────────────────────────────────────────────────
    def fader(self, level):
        """Volume. 0.0 = silent, 1.0 = unity; clamped to [0, 1]."""
        v = max(0.0, min(1.0, float(level)))
        self.s.b.request(self._ns("set_volume"), {"index": self.idx, "value": v})
        time.sleep(0.03); return self

    def automate(self, param, points, remote_index=0, page=None):
        """Offline automation. param: 'volume' | 'pan' | 'remote'. points: [(beat, val0..1
        [, curvature, 'linear'|'hold']), ...]. For 'remote', `remote_index` is the slot on
        the device's CURRENT remote page; pass `page` to target a non-default page (select
        the device first)."""
        self.select()
        if page is not None:
            self.s.b.request("device.select_remote_page", {"page": int(page)}); time.sleep(0.1)
        wa.write_offline(self.s.b, [list(p) for p in points], param=param, remote_index=remote_index)
        self._auto_spec.append({"param": param, "points": [list(p) for p in points],
                                "remote_index": remote_index, "page": page})
        return self


class Song:
    def __init__(self, tempo=128, bars=16, bridge=None, clean=False,
                 *, check_version=True):
        if bridge is not None:
            self.b = bridge; self._own_bridge = False
        else:
            self.b = BridgeClient(); self.b.start(); self._own_bridge = True
            if not self.b.wait_connected(8):
                raise BridgeError("bridge not connected - is Bitwig running with the "
                                  "OpenwigBridge controller enabled? (Settings -> Controllers)")
        # Refuse to run against an unsupported Bitwig version - the SDK reaches
        # into private internals that move across point releases. Set
        # check_version=False to bypass (advanced, may crash).
        if check_version:
            from openwig import SUPPORTED_BITWIG_VERSIONS
            self.b.ensure_compatible(SUPPORTED_BITWIG_VERSIONS)
        self.tempo = tempo; self.bars = bars; self.beats = bars * 4; self.total = self.beats
        self.tracks = {}
        self.b.request("transport.stop")
        if clean:                                  # delete all tracks + master FX -> clean slate
            self.clear()
        try: self.b.request("transport.set_automation_write", {"on": False})
        except Exception: pass
        try: self.b.request("transport.set_tempo", {"bpm": float(tempo)})
        except Exception: pass
        time.sleep(0.2)

    def clear(self):
        """Clean slate: delete all tracks (incl. send/effect tracks) + master FX."""
        try:
            self.b.request("project.clear")
        except Exception:
            pass
        time.sleep(0.5)
        return self

    def track(self, name, device=None, uuid=None, kind="instrument"):
        t = Track(self, name, device=device, uuid=uuid, kind=kind); self.tracks[name] = t; return t

    def audio_track(self, name):
        """Audio track (for sample/loop clips). Returns a Track."""
        return self.track(name, kind="audio")

    def fx_track(self, name, device=None):
        """Effect/return track (destination for sends)."""
        return self.track(name, kind="effect", device=device)

    # ── transport / undo / metronome ─────────────────────────────────────────
    def set_tempo(self, bpm):
        self.b.request("transport.set_tempo", {"bpm": float(bpm)})
        self.tempo = bpm; time.sleep(0.05); return self

    def metronome(self, on=True):
        self.b.request("transport.set_metronome", {"on": bool(on)}); return self

    def undo(self, n=1):
        for _ in range(n): self.b.request("app.undo"); time.sleep(0.05)
        return self

    def redo(self, n=1):
        for _ in range(n): self.b.request("app.redo"); time.sleep(0.05)
        return self

    def panel(self, layout="ARRANGE"):
        """layout: 'ARRANGE' | 'MIX' | 'EDIT' | 'PLAY'."""
        self.b.request("app.set_panel_layout", {"layout": layout}); return self

    def verbose(self, on=True):
        """Trace every bridge call to stdout (for debugging)."""
        if on and not getattr(self.b, "_traced", False):
            orig = self.b.request
            def traced(method, params=None):
                print(f"  -> {method} {params or {}}")
                return orig(method, params)
            self.b.request = traced; self.b._traced = True
        return self

    def marker(self):
        """Drop a cue marker at the current playhead. Bitwig names it automatically -
        the controller call (Transport.addCueMarkerAtPlaybackPosition) takes no name,
        so naming is not supported."""
        self.b.request("cue.add")
        time.sleep(0.05); return self

    def scene_launch(self, scene_index):
        self.b.request("scene.launch", {"scene": int(scene_index)}); return self

    def stop_all(self):
        for t in self.tracks.values():
            self.b.request("track.stop", {"index": t.idx})
        return self

    # ── project save / open (action-driven; ApplicationProxy doesn't expose
    #    saveProject/openProject - these trigger Bitwig's GUI actions, which means
    #    save-as / open prompt the OS file dialog). For headless save/load you'd
    #    need deeper RE into Bitwig's internal project I/O. ──
    def save(self):
        """Save the project in place. Use 'Save As...' from the GUI for new files."""
        self.b.request("project.save"); time.sleep(0.5); return self

    def save_as_dialog(self):
        """Pop the Save-As file dialog (user picks path)."""
        self.b.request("project.save_as"); time.sleep(0.3); return self

    def open_dialog(self):
        """Pop the Open-Project file dialog."""
        self.b.request("project.open_dialog"); time.sleep(0.3); return self

    def new_project(self):
        self.b.request("project.new"); time.sleep(1.0); return self

    # ── tempo automation (uses the new tempo.write_offline bridge handler) ──
    def automate_tempo(self, points):
        """Tempo automation. points: [(beat, bpm), ...] - bpm is converted to the
        normalized 0..1 tempo-atom value (20..666 BPM)."""
        norm = [(b, max(0.0, min(1.0, (bpm - 20) / 646.0))) for (b, bpm) in points]
        self.b.request("tempo.write_offline", {"points": [list(p) for p in norm]})
        time.sleep(0.5); return self

    def master(self, chain, tune=None):
        """Build the master FX chain. Each item is a factory device NAME (e.g. 'EQ+') or
        a PATH to a .bwpreset/.bwdevice file (absolute, or containing a separator). tune:
        optional {device_name: {remote_substr: value}} dialed after inserting that device."""
        tune = tune or {}
        self._master_spec = {"chain": list(chain), "tune": dict(tune)}
        for dev in chain:
            is_path = ("/" in dev or "\\" in dev or dev.endswith((".bwpreset", ".bwdevice")))
            path = dev if is_path else f"{FACTORY}/{dev}.bwdevice"
            self.b.request("device.insert_file_on_master", {"path": path}); time.sleep(1.0)
            for sub, val in (tune.get(dev) or {}).items():
                for r in self.b.request("master.remotes"):
                    if r.get("exists") and sub.lower() in ("" + (r.get("name") or "")).lower():
                        self.b.request("master.set_remote", {"index": r["index"], "value": val}); break
        return self

    def play(self, loop=True):
        self.b.request("transport.set_position", {"beats": 0.0})
        try:
            self.b.request("transport.set_loop", {"on": bool(loop)})
            if loop: self.b.request("transport.set_loop_region", {"start": 0.0, "length": float(self.total)})
        except Exception: pass
        self.b.request("transport.play"); return self

    def stop(self):
        self.b.request("transport.stop"); return self

    def render(self, path):
        """Render the arrangement to `path` (.wav) via WASAPI loopback. Returns a dict:
        {path, seconds, rate, channels, rms, silent} - check `silent`/`rms` to confirm
        it actually captured sound."""
        return render_to_wav(self.b, path, beats=self.total, tempo=self.tempo)

    def close(self):
        if self._own_bridge: self.b.stop()
