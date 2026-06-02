# Changelog

All notable changes to openwig are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
