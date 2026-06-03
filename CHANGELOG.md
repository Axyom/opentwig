# Changelog

All notable changes to openwig are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-06-03

Slimmed to a barebones core: openwig is now a thin layer over Bitwig where you
write your own Python. **This release removes API surface** (see below).

### Added
- `Note` named tuple - `Note(key, start, dur=0.5, vel=1.0, channel=0)`, fully
  interchangeable with raw `(key, start, dur, vel)` tuples. Exported from `openwig`.

### Removed (breaking)
- Helper submodules `notes`, `curves`, `arrangement`, `export`, `lint`, `live`.
- `Song.pulse()` and `Track.pump()`.
- MIDI / JSON export and JSON round-trip (was in the `export` module).
- Bring your own note/pattern/curve generation - it's ordinary Python.

### Changed
- `render()` returns a dict `{path, seconds, rate, channels, rms, silent}`
  (previously documented, incorrectly, as returning a path).
- `fader()` clamps `level` to `[0, 1]` (matching `pan`).
- `arm` / `monitor` / `send` on an effect track, and an unmatched `fx()` remote
  name, now emit a `warnings.warn` instead of silently doing nothing.
- `transpose_cursor` / `quantize_cursor` / `step_attr` select the track first,
  so they act on that track's clip regardless of call order.

### Fixed
- `Song(...)` raises `BridgeError` instead of `assert` when the bridge is
  unreachable (survives `python -O`).
- `clip()` raises a clear `ValueError` on a note with fewer than 4 fields.
- `marker()` dropped its non-functional `name` argument (the controller's
  add-marker call takes no name).
- Documentation corrected: the "all methods return `self`" claim and the
  `render()` return type.

### Docs
- Documentation site trimmed to Home / Install / Quickstart / API reference
  (removed Changelog / Cookbook / Compatibility / Tutorials tabs).

## [0.1.0] - 2026-06-02

Initial open-source release (GPL-3.0-or-later).

### Added
- `Song` / `Track` declarative API for building arrangements.
- Device loading by factory file path or Bitwig UUID.
- Clip + note insertion via the controller bridge (`clip.create_arranger_with_notes`).
- Offline automation for volume / pan / device remote-control parameters.
- FX track addressing (`fxtrack.*` handlers, bank-aware `Track`).
- Modulator insertion + mapping.
- Sidechain wiring between devices on different tracks.
- Arranger audio clip insertion.
- Render-to-wav via WASAPI loopback (Windows).
- MIDI / JSON export and JSON round-trip.
- `openwig` CLI: `install`, `uninstall`, `doctor`.
- Bitwig version handshake - refuses to connect on mismatch.

### Compatibility
- Bitwig Studio **6.0.6** only.
- Python 3.11+.
- Windows (primary), macOS / Linux (best-effort; loopback render is Windows-only).

### Known gaps
- Track input / output routing (no Bitwig API setter; GUI-only).
- Per-clip automation lanes (API unexplored).
- Arranger NoteStep attributes (API is launcher-only).
- Path-headless project save-as / open (action API exposes GUI dialog only).
