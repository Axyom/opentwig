"""bitwig_export.py - render, stem-export, MIDI-export, song save/load helpers.

All take a Song (with a live bridge) + Tracks that have accumulated clip specs.
For .mid export to work we read notes from the Song's `_clip_specs` (populated
by Track._make_clip in bitwig_song.py).
"""
from __future__ import annotations
import json, time, wave
from pathlib import Path
import numpy as np

from openwig.wire.render import render_to_wav


# ── sectional + stem render ──────────────────────────────────────────────────

def render_section(song, out_path, *, start_beat: float, end_beat: float, tempo: float | None = None):
    """Render a portion of the arrangement (loopback capture starting at start_beat).
    Stops, repositions, plays the section once."""
    if tempo is None: tempo = song.tempo
    beats = end_beat - start_beat
    # render_to_wav starts at beat 0 - temporarily override by positioning first
    song.b.request("transport.stop"); time.sleep(0.1)
    song.b.request("transport.set_position", {"beats": float(start_beat)})
    # Run the same loopback pump, but start from the current playhead:
    res = _capture(song.b, out_path, beats=beats, tempo=tempo,
                   set_position_to=start_beat)
    return res


def render_stems(song, out_dir, *, tempo: float | None = None):
    """Render each track to its own .wav by soloing one at a time.
    Restores solo state on completion (best-effort)."""
    if tempo is None: tempo = song.tempo
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    snap = song.b.request("state.snapshot")
    names = [(t["index"], t["name"]) for t in snap.get("tracks", [])]
    results = []
    for idx, name in names:
        # solo only this track
        for j, _ in names:
            song.b.request("track.set_solo", {"index": j, "on": j == idx})
            time.sleep(0.03)
        time.sleep(0.2)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        path = out_dir / f"{idx:02d}_{safe}.wav"
        r = render_to_wav(song.b, str(path), beats=song.total, tempo=tempo)
        results.append({"track": name, **r})
    # clear all solos
    for j, _ in names:
        song.b.request("track.set_solo", {"index": j, "on": False}); time.sleep(0.03)
    return results


def _capture(bridge, out_path, *, beats, tempo, set_position_to=0.0, tail=1.0, lead=0.3):
    """Internal: identical to render_to_wav but starts at set_position_to."""
    import pyaudiowpatch as pa
    secs = beats / tempo * 60.0 + tail
    p = pa.PyAudio()
    dev = p.get_default_wasapi_loopback()
    sr = int(dev["defaultSampleRate"]); ch = int(dev["maxInputChannels"]); di = dev["index"]
    frames_per = 2048
    st = p.open(format=pa.paInt16, channels=ch, rate=sr, input=True,
                input_device_index=di, frames_per_buffer=frames_per)
    try: bridge.request("transport.set_loop", {"on": False})
    except Exception: pass
    bridge.request("transport.stop"); time.sleep(0.2)
    bridge.request("transport.set_position", {"beats": float(set_position_to)})
    st.read(max(1, st.get_read_available()), exception_on_overflow=False)
    bridge.request("transport.play"); time.sleep(lead)
    chunks = []; got = 0; need = int(secs * sr)
    while got < need:
        data = st.read(frames_per, exception_on_overflow=False)
        chunks.append(data); got += frames_per
    bridge.request("transport.stop")
    st.stop_stream(); st.close(); p.terminate()
    audio = np.frombuffer(b"".join(chunks), dtype=np.int16)
    rms = float(np.sqrt(np.mean((audio.astype(np.float64) / 32768.0) ** 2)))
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(chunks))
    return {"path": str(out_path), "seconds": round(len(audio)/ch/sr, 2), "rate": sr,
            "channels": ch, "rms": round(rms, 5), "silent": rms < 1e-3}


# ── MIDI .mid file export ────────────────────────────────────────────────────

def _vlq(n: int) -> bytes:
    """MIDI variable-length quantity."""
    buf = [n & 0x7F]; n >>= 7
    while n:
        buf.append((n & 0x7F) | 0x80); n >>= 7
    return bytes(reversed(buf))


def export_midi(song, out_path, *, ppq: int = 480):
    """Write all the song's clips as a single multi-track .mid file (Type 1).
    Each Track in song.tracks becomes one MIDI track. Uses Song's _clip_specs
    (populated by _make_clip)."""
    out_path = str(out_path); tracks_data = []
    # tempo meta in first conductor track
    tempo_us = int(60_000_000 / song.tempo)
    cond = bytes([0x00, 0xFF, 0x51, 0x03]) + tempo_us.to_bytes(3, "big") + bytes([0x00, 0xFF, 0x2F, 0x00])
    tracks_data.append(cond)

    for name, track in song.tracks.items():
        events = []        # (abs_ticks, bytes)
        for (start, dur, notes) in getattr(track, "_clip_specs", []):
            for nt in notes:
                key = int(nt[0]); st = float(nt[1]); du = float(nt[2])
                vel = max(1, min(127, int(127 * float(nt[3]))))
                ch = (int(nt[4]) if len(nt) > 4 else 0) & 0x0F
                on_t  = int((start + st) * ppq)
                off_t = int((start + st + du) * ppq)
                events.append((on_t,  bytes([0x90 | ch, key, vel])))
                events.append((off_t, bytes([0x80 | ch, key, 0])))
        events.sort(key=lambda e: e[0])
        # delta-encode
        body = bytearray()
        body += b"\x00\xFF\x03" + bytes([len(name)]) + name.encode("ascii", "replace")[:255]
        prev = 0
        for abs_t, data in events:
            body += _vlq(abs_t - prev) + data
            prev = abs_t
        body += b"\x00\xFF\x2F\x00"
        tracks_data.append(bytes(body))

    with open(out_path, "wb") as f:
        f.write(b"MThd" + (6).to_bytes(4, "big"))
        f.write((1).to_bytes(2, "big") + len(tracks_data).to_bytes(2, "big") + ppq.to_bytes(2, "big"))
        for tdata in tracks_data:
            f.write(b"MTrk" + len(tdata).to_bytes(4, "big") + tdata)
    return out_path


# ── declarative round-trip: Song <-> dict / JSON ────────────────────────────

def to_dict(song):
    """Serialize a Song's spec (tempo, tracks, devices, clip specs, master) so it
    can be reloaded with `from_dict`. Only captures what the SDK was told to build
    (not Bitwig-side edits the user made afterwards)."""
    spec = {
        "tempo": song.tempo, "bars": song.bars,
        "tracks": [], "master": getattr(song, "_master_spec", None),
    }
    for name, t in song.tracks.items():
        spec["tracks"].append({
            "name": name, "kind": t.kind,
            "device": getattr(t, "_device_name", None),
            "uuid":   getattr(t, "_device_uuid", None),
            "fx":     list(getattr(t, "_fx_spec", [])),
            "clips":  [{"start": s, "dur": d, "notes": list(map(list, n))}
                       for (s, d, n) in getattr(t, "_clip_specs", [])],
            "automation": list(getattr(t, "_auto_spec", [])),
        })
    return spec


def from_dict(spec, *, song_cls=None, bridge=None, clean=True):
    """Rebuild a Song from a spec dict (output of to_dict). Imports lazily to
    avoid a circular import with bitwig_song."""
    if song_cls is None:
        from openwig.song import Song
        song_cls = Song
    s = song_cls(tempo=spec.get("tempo", 128), bars=spec.get("bars", 16),
                 bridge=bridge, clean=clean)
    for td in spec.get("tracks", []):
        t = s.track(td["name"], device=td.get("device"), uuid=td.get("uuid"),
                    kind=td.get("kind", "instrument"))
        for fx in td.get("fx", []):
            t.fx(fx["name"], **fx.get("remotes", {}))
        for clip in td.get("clips", []):
            t._make_clip(clip["start"], clip["dur"],
                         [tuple(n) for n in clip["notes"]])
        for auto in td.get("automation", []):
            t.automate(auto["param"], [tuple(p) for p in auto["points"]],
                       remote_index=auto.get("remote_index", 0))
    if spec.get("master"):
        s.master(spec["master"].get("chain", []), tune=spec["master"].get("tune"))
    return s


def save_json(song, path):
    path = str(path)
    Path(path).write_text(json.dumps(to_dict(song), indent=2))
    return path


def load_json(path, *, bridge=None, clean=True):
    spec = json.loads(Path(path).read_text())
    return from_dict(spec, bridge=bridge, clean=clean)


# ── post-render LUFS / true-peak measurement ─────────────────────────────────

def measure_loudness(wav_path):
    """Quick loudness measurement on a rendered .wav. Returns RMS dBFS + true-peak.
    Not strict ITU BS.1770 (no K-weighting); good enough for relative comparisons."""
    with wave.open(str(wav_path), "rb") as w:
        ch = w.getnchannels(); sr = w.getframerate(); n = w.getnframes()
        raw = w.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2: audio = audio.reshape(-1, 2).mean(axis=1)
    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return {
        "rms_dbfs":  round(20 * np.log10(rms + 1e-9), 2),
        "peak_dbfs": round(20 * np.log10(peak + 1e-9), 2),
        "duration_s": round(len(audio) / sr, 2),
        "sample_rate": sr,
    }
