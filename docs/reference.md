# API reference

## Song

```python
from openwig import Song
s = Song(tempo=128, bars=16, clean=True)
```

| Method | Description |
|---|---|
| `Song(tempo, bars, clean, check_version)` | Connect to Bitwig. `clean=True` wipes the project first. |
| `s.track(name, device, uuid, kind)` | Create an instrument track. Returns `Track`. |
| `s.audio_track(name)` | Create an audio track. Returns `Track`. |
| `s.fx_track(name, device)` | Create an effect/return track. Returns `Track`. |
| `s.master(chain, tune)` | Build the master FX chain, e.g. `["EQ+", "Compressor+", "Peak Limiter"]`. |
| `s.pulse(key, step, off, dur, vel)` | Generate a repeating note list for the whole song. |
| `s.play(loop)` | Start transport. `loop=True` loops the arrangement. |
| `s.stop()` | Stop transport. |
| `s.render(path)` | Render to `.wav` via WASAPI loopback. Returns absolute path. |
| `s.clear()` | Delete all tracks and master FX. |
| `s.set_tempo(bpm)` | Change tempo live. |
| `s.automate_tempo(points)` | Tempo automation. `points`: `[(beat, bpm), ...]`. |
| `s.metronome(on)` | Toggle metronome. |
| `s.undo(n)` / `s.redo(n)` | Undo/redo N steps. |
| `s.panel(layout)` | Switch panel: `"ARRANGE"` / `"MIX"` / `"EDIT"` / `"PLAY"`. |
| `s.marker(name)` | Drop a cue marker at the current playhead. |
| `s.scene_launch(index)` | Launch a scene. |
| `s.stop_all()` | Stop all clip slots. |
| `s.save()` | Save project in place. |
| `s.save_as_dialog()` | Open Save-As dialog. |
| `s.open_dialog()` | Open project picker dialog. |
| `s.new_project()` | Create a new empty project. |
| `s.verbose(on)` | Print every bridge call to stdout (debugging). |
| `s.close()` | Disconnect from the bridge. |

---

## Track

All methods return `self` - they are chainable.

### Channel strip

| Method | Description |
|---|---|
| `t.fader(level)` | Volume. `0.0` = silent, `1.0` = unity. |
| `t.pan(value)` | Pan. `-1.0` = full left, `0.0` = center, `1.0` = full right. |
| `t.mute(on)` | Mute. |
| `t.solo(on)` | Solo. |
| `t.arm(on)` | Record-arm (instrument/audio tracks only). |
| `t.monitor(mode)` | Monitor mode: `"OFF"` / `"AUTO"` / `"ON"`. |
| `t.color(r, g, b, a)` | Track color. All values `0.0..1.0`. |
| `t.send(send_index, value)` | Send level to an effect track. |
| `t.rename(name)` | Rename the track. |

### Clips

| Method | Description |
|---|---|
| `t.clip(notes, dur, start)` | One arranger clip spanning the song (or `dur` beats from `start`). |
| `t.clips(segments)` | Multiple arranger clips. `segments`: `[(start, dur, notes), ...]`. |
| `t.scene(slot, notes, dur, step_size)` | Launcher clip in slot `slot`. |
| `t.launch(slot)` | Launch launcher slot. |
| `t.audio_clip(path, start, duration)` | Drop a `.wav`/`.aiff` onto the arranger. |
| `t.audio_clips(segments)` | Multiple audio clips: `[(path, start, dur), ...]`. |
| `t.sample(path, slot)` | Load audio into a launcher slot (audio tracks). |
| `t.transpose_cursor(semitones)` | Transpose the selected clip. |
| `t.quantize_cursor(amount)` | Quantize the selected clip's notes (`0..1`). |
| `t.step_attr(x, key, attr, value)` | Set a per-note attribute on a step-grid clip. `attr`: `"velocity"` / `"chance"` / `"pan"` / `"timbre"` / `"pressure"` / `"duration"` / `"release"` / `"transpose"` / `"gain"`. |
| `t.describe_clip()` | List all property IDs on the selected clip (discovery). |
| `t.set_clip_prop(prop_id, value)` | Set a clip property by ID (discovered via `describe_clip`). |

### Devices

| Method | Description |
|---|---|
| `t.fx(name, **remotes)` | Insert a factory device and tune remotes, e.g. `fx("Reverb", Mix=0.3)`. |
| `t.add_device(name)` | Insert a factory device by name. |
| `t.add_bitwig(uuid)` | Insert a Bitwig device by UUID. |
| `t.preset(path)` | Load a `.bwpreset` file (replaces the device chain). |
| `t.delete_device()` | Delete the currently-selected device. |
| `t.select_device(index)` | Select device at position `index` in the chain. |
| `t.remote_pages()` | List all remote control pages of the cursor device. |

### Automation and mix

| Method | Description |
|---|---|
| `t.automate(param, points, remote_index)` | Offline automation. `param`: `"volume"` / `"pan"` / `"remote"`. `points`: `[(beat, value), ...]`. |
| `t.pump(active, hi, duck)` | Volume-duck on every beat (sidechain-style). `hi`: peak level, `duck`: trough level. |

### Modulators and routing

| Method | Description |
|---|---|
| `t.add_modulator(name, x, y)` | Insert a modulator (e.g. `"LFO"`, `"Steps"`, `"ADSR"`). |
| `t.map_modulator(source_index, dest, remote_index, amount)` | Wire a modulator to `"volume"` / `"pan"` / `"remote"`. |
| `t.list_modulators()` | List modulation sources on the current device. |
| `t.sidechain_from(source_track, source_device_index, sink_device_index)` | Wire a sidechain input (e.g. Compressor+ listening to the kick). |
| `t.routing_info()` | Read current routing state (read-only). |

---

## openwig.notes

Generators return a note list: `[(key, start_beat, duration, velocity), ...]`.

| Function | Description |
|---|---|
| `euclidean(key, pulses, steps, step_beats, ...)` | Euclidean rhythm - spread `pulses` hits across `steps` slots. |
| `markov(seed_notes, length, ...)` | Markov-chain melody from a seed pitch list. |
| `weighted(choices, length, ...)` | Random notes from a `[(key, weight), ...]` table. |
| `arp(keys, pattern, start, length, step, dur, vel)` | Arpeggio. `pattern`: `"up"` / `"down"` / `"updown"` / `"random"`. |
| `chord_notes(root, quality, start, dur, vel)` | One chord as simultaneous notes. |
| `progression(qualities_in_root, ...)` | Sequence of chords as a note list. |
| `scale(root, mode, octaves)` | List of MIDI keys in a scale. |
| `chord(root, quality, octave_shift)` | List of MIDI keys in a chord. |
| `note_to_midi(name)` | `"C4"` -> `60`. |

Transforms (all take a note list, return a note list):

| Function | Description |
|---|---|
| `transpose(notes, semitones)` | Shift all pitches. |
| `quantize(notes, grid)` | Snap start times to a grid. |
| `humanize(notes, time, vel, seed)` | Add small random timing and velocity offsets. |
| `swing(notes, amount, grid)` | Apply swing to off-beat notes. |
| `stretch(notes, factor)` | Scale all timings. |
| `retrograde(notes, length)` | Reverse the note list. |
| `invert(notes, axis_key)` | Mirror pitches around `axis_key`. |
| `repeat(notes, times, gap, length)` | Tile a note list `times` over. `length` = block size in beats (default: max note end). |
| `shift(notes, beats)` | Move all notes in time. |
| `velocity_scale(notes, factor)` | Scale all velocities. |
| `merge(*note_lists)` | Combine multiple note lists. |
| `ascii_roll(notes, length, grid)` | Print a piano-roll to stdout. |

---

## openwig.curves

Curve functions return automation points: `[(beat, value), ...]` for use with `Track.automate`.

| Function | Description |
|---|---|
| `lfo(span, shape, rate, lo, hi, phase)` | LFO. `shape`: `"sine"` / `"tri"` / `"saw"` / `"square"` / `"ramp"`. |
| `ramp(start_val, end_val, start_beat, end_beat)` | Linear ramp between two values. |
| `env_adsr(span, attack, decay, sustain, release, peak)` | ADSR envelope. |
| `sample_hold(span, rate, lo, hi, seed)` | Random step function. |
| `gate(active_ranges, off, on)` | `1.0` inside ranges, `0.0` outside. |
| `sidechain_duck(span, every, recover, hi, duck)` | Repeating volume duck (same shape as `Track.pump` but as a raw curve). |
| `follow_rms(wav_path, bin_beats, tempo, lo, hi)` | Drive automation from the RMS envelope of an audio file. |

---

## openwig.arrangement

Pattern builders (return note lists):

| Function | Description |
|---|---|
| `four_on_floor(length, key, step, dur, vel)` | Kick on every beat. |
| `offbeat_hats(length, key, step, dur, vel)` | Hats on off-beats. |
| `backbeat_clap(length, key, dur, vel)` | Clap on beats 2 and 4. |
| `walking_bass(length, scale_keys, step, dur, vel)` | Stepwise bass line through a scale. |

Genre templates (build a full song directly onto a `Song` object):

| Function | Description |
|---|---|
| `techno(song, bars, root, mode)` | 4-track techno template. |
| `house(song, bars, root, mode)` | House template with offbeat bass. |
| `dnb(song, bars, root, mode)` | Drum and bass template. |
| `lofi(song, bars, root, mode)` | Lo-fi hip-hop template. |
| `ambient(song, bars, root, mode)` | Sparse ambient template. |
| `template(song, name, **kwargs)` | Call any template by name. |

Utilities:

| Function | Description |
|---|---|
| `section(song, name, start, length)` | Scope helper - wraps a time window. Use `.fill(track, notes)` and `.each(tracks, fn)` on the returned object. |
| `transaction(song)` | Context manager - wrap multiple edits in one undo step. |
| `reharm(notes, original_root, original_mode, new_progression)` | Snap a melody to a new chord progression. |
| `print_timeline(song, beat_width)` | Print an ASCII clip layout to stdout. |

---

## openwig.export

| Function | Description |
|---|---|
| `render_section(song, out_path, start_beat, end_beat, tempo)` | Render a time range to `.wav`. |
| `render_stems(song, out_dir, tempo)` | Render each track to a separate `.wav` in `out_dir`. |
| `export_midi(song, out_path, ppq)` | Export all clips as a standard MIDI file. |
| `to_dict(song)` | Serialize the song to a Python dict. |
| `from_dict(spec, bridge, clean)` | Rebuild a song from a dict. |
| `save_json(song, path)` | Save `to_dict` output as JSON. |
| `load_json(path, bridge, clean)` | Load and replay a JSON song file. |
| `measure_loudness(wav_path)` | Return RMS loudness of a `.wav` (for verification). |
