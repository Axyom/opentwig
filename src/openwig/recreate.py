"""openwig.recreate - emit a Python script that recreates the OPEN project.

    from openwig.bridge import BridgeClient
    from openwig.read import read_project
    from openwig.recreate import to_script

    b = BridgeClient(); b.start(); b.wait_connected(8)
    data = read_project(b, with_clips=True)
    open("my_song.py", "w").write(to_script(data, project_label="my_song"))

Or just `python -m openwig recreate -o my_song.py`.

What survives the round-trip (high fidelity):
    - tempo, track names + mix (fader, pan, mute/solo/arm)
    - device chain by name + active remote-control values
    - MIDI clips (start, duration, notes with channel/velocity)
    - arranger automation lanes (volume, pan)
    - effect/return tracks

Gaps (emitted as comments where relevant):
    - track input/output routing, send levels (not in snapshot)
    - sidechain wiring, modulators (read API gap)
    - per-clip automation lanes (only arranger lanes are read)
    - VST plugin internal state (opaque)
    - master device chain (read API doesn't surface it yet)
"""
from __future__ import annotations

import datetime as _dt
import keyword
import os as _os
import re
from typing import Any


def _norm(s):
    return "".join(c for c in str(s).lower() if c.isalnum())


def _bitwig_dirs():
    """(factory devices dir or None, [preset dirs]). Presets live in BOTH the
    install library and the user library (~/Documents/Bitwig Studio/Library)."""
    factory, preset_dirs = None, []
    try:
        from openwig.song import FACTORY
        if _os.path.isdir(FACTORY):
            factory = FACTORY
            p = _os.path.join(_os.path.dirname(FACTORY), "Presets")
            if _os.path.isdir(p):
                preset_dirs.append(p)
    except Exception:
        pass
    user = _os.path.expanduser(_os.path.join("~", "Documents", "Bitwig Studio", "Library", "Presets"))
    if _os.path.isdir(user):
        preset_dirs.append(user)
    return factory, preset_dirs


def _build_preset_index(preset_dirs):
    """{normalized preset name -> .bwpreset path} across all preset directories."""
    idx = {}
    for d in (preset_dirs or []):
        try:
            for root, _dirs, files in _os.walk(d):
                for f in files:
                    if f.endswith(".bwpreset"):
                        idx.setdefault(_norm(f[:-len(".bwpreset")]), _os.path.join(root, f))
        except Exception:
            pass
    return idx


def _resolve_device(name, factory_dir, preset_idx):
    """How to load a device by its chain name: ('factory', None) | ('preset', path) | ('unknown', None)."""
    if not factory_dir:
        return "factory", None      # can't verify (no Bitwig library) -> assume factory by name
    if _os.path.exists(f"{factory_dir}/{name}.bwdevice"):
        return "factory", None
    p = preset_idx.get(_norm(name))
    if p:
        return "preset", p
    return "unknown", None


def _remote_kwargs(d):
    parts = []
    for r in [r for r in (d.get("remotes") or []) if r.get("name")][:4]:
        val = r.get("value")
        if isinstance(val, (int, float)):
            parts.append(f"{_pyident(r['name'], 'p')}={_fmt_float(val)}")
    return ", ".join(parts)

_INSTRUMENT_HINTS = (
    "Polysynth", "FM-", "Phase-", "Sampler", "Drum Machine", "Kick", "Clap",
    "Hat", "Snare", "Hi-hat", "Conga", "Bongo", "Tom",
)
_NAMING_FALLBACK = re.compile(r"[^A-Za-z0-9_]+")


def _pyident(name: str, fallback: str) -> str:
    s = _NAMING_FALLBACK.sub("_", name or "").strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "_" + s
    if keyword.iskeyword(s):
        s = s + "_"
    return s


def _classify_track(t: dict) -> str:
    devs = t.get("devices") or []
    if devs and any(any(h in (d.get("name") or "") for h in _INSTRUMENT_HINTS) for d in devs):
        return "instrument"
    if not devs:
        return "audio"
    return "instrument"


def _fmt_float(x: Any, default: float = 0.0, places: int = 4) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return repr(default)
    s = f"{f:.{places}f}".rstrip("0").rstrip(".")
    return s if s and s not in ("-", "-0") else "0"


# Arranger automation breakpoints are stored in each parameter's RAW/native units,
# but Track.automate() expects normalized 0..1. The native<->normalized map is affine
# (verified across pitch/freq/dB/%/volume/pan): normalized = (raw - off) / scale.
# Volume and pan use fixed Bitwig constants; device remotes are calibrated live (the
# read step measures off/scale per target and stores them on the automation target).
_VOL_OFF, _VOL_SCALE = 0.0, 1.2599
_PAN_OFF, _PAN_SCALE = -1.0, 2.0


def _norm_bps(bps, off, scale):
    """Format breakpoints as (time, value) tuples, mapping raw value -> normalized
    via the affine (off, scale) and clamping to 0..1. scale None/0 -> values kept raw."""
    out = []
    for b in bps[:512]:
        v = b.get("value")
        if scale and isinstance(v, (int, float)):
            v = max(0.0, min(1.0, (v - off) / scale))
        out.append(f"({_fmt_float(b.get('time'))}, {_fmt_float(v)})")
    return ", ".join(out)


def _pan_to_signed(p):
    """Snapshot pan is 0..1 (0 = full left, 0.5 = center). Track.pan takes -1..+1."""
    try:
        return max(-1.0, min(1.0, (float(p) - 0.5) * 2.0))
    except (TypeError, ValueError):
        return 0.0


def _emit_header(project_label: str, data: dict) -> str:
    n_tracks = len(data.get("tracks") or [])
    n_fx = len(data.get("effect_tracks") or [])
    total_clips = sum(len(t.get("clips") or []) for t in data.get("tracks", []))
    total_notes = sum(
        c.get("note_count") or len(c.get("notes") or [])
        for t in data.get("tracks", []) for c in (t.get("clips") or []))
    gaps = []
    for t in data.get("tracks", []):
        for d in (t.get("devices") or []):
            nm = d.get("name") or ""
            if "VST" in nm or nm.startswith("Plugin"):
                gaps.append(f"track {t['name']!r}: {nm!r} (plugin internal state not recreated)")
        if t.get("clips_error"):
            gaps.append(f"track {t['name']!r}: clip read failed ({t['clips_error']})")
    today = _dt.date.today().isoformat()
    gap_block = ("\n  - " + "\n  - ".join(gaps)) if gaps else " (none)"
    return f'''"""Recreated from the open Bitwig project ({project_label}).
Generated: {today} by openwig.recreate (live read).

Coverage: tempo, tracks ({n_tracks}), effect tracks ({n_fx}), MIDI clips
({total_clips}), notes ({total_notes}), arranger automation, device chains +
remote values.

NOT recreated (read-API gaps): track input/output routing, send levels,
sidechain wiring, modulators, per-clip automation, plugin internal state, and
the master device chain (fill in s.master([...]) yourself).

Per-project notes:{gap_block}
"""
'''


def _emit_song_open(data: dict) -> list[str]:
    tempo = data.get("tempo") or 120.0
    max_end = 0.0
    for t in data.get("tracks", []):
        for c in t.get("clips", []) or []:
            try:
                max_end = max(max_end, float(c.get("clip_start") or 0) + float(c.get("clip_duration") or 0))
            except (TypeError, ValueError):
                pass
    bars = max(1, int(((max_end + 3.99) // 4)))
    return ["from openwig import Song", "",
            f"s = Song(tempo={_fmt_float(tempo, 120.0)}, bars={bars}, clean=True)", ""]


def _emit_track(t: dict, var: str, factory_dir=None, preset_idx=None) -> list[str]:
    preset_idx = preset_idx or {}
    lines: list[str] = []
    name = (t.get("name") or "").strip() or f"track_{t.get('index')}"
    kind = _classify_track(t)
    devs = t.get("devices") or []

    if kind == "audio":
        lines.append(f"{var} = s.audio_track({name!r})")
    else:
        first_dev = devs[0] if devs else None
        dkind, dpath = _resolve_device(first_dev["name"], factory_dir, preset_idx) if first_dev else ("none", None)
        if dkind == "factory":
            lines.append(f"{var} = s.track({name!r}, device={first_dev['name']!r})")
        elif dkind == "preset":
            lines.append(f"{var} = s.track({name!r})")
            lines.append(f"{var}.preset({dpath!r})   # {first_dev['name']}")
            kw = _remote_kwargs(first_dev)
            if kw:
                lines.append(f"{var}.set_remotes({kw})")
        else:
            lines.append(f"{var} = s.track({name!r})")
            if first_dev:
                lines.append(f"# instrument {first_dev['name']!r}: no factory device / preset found - load manually")

    vol = t.get("volume")
    if vol is not None:
        lines.append(f"{var}.fader({_fmt_float(vol)})")
    pan = _pan_to_signed(t.get("pan", 0.5))
    if abs(pan) > 1e-4:
        lines.append(f"{var}.pan({_fmt_float(pan)})")
    if t.get("mute"): lines.append(f"{var}.mute(True)")
    if t.get("solo"): lines.append(f"{var}.solo(True)")
    if t.get("arm"):  lines.append(f"{var}.arm(True)")

    for d in devs[1:]:                       # FX chain = everything after the first device
        dname = d.get("name") or ""
        if not dname:
            continue
        dkind, dpath = _resolve_device(dname, factory_dir, preset_idx)
        if dkind == "factory":
            kw = _remote_kwargs(d)
            lines.append(f"{var}.fx({dname!r}" + ((", " + kw) if kw else "") + ")")
        elif dkind == "preset":
            lines.append(f"{var}.preset({dpath!r})   # {dname}")
            kw = _remote_kwargs(d)
            if kw:
                lines.append(f"{var}.set_remotes({kw})")
        else:
            lines.append(f"# {var}: {dname!r} - no factory device / preset found; load manually")

    clips = t.get("clips") or []
    for ci, c in enumerate(clips):
        start = c.get("clip_start") or 0.0
        dur = c.get("clip_duration") or 4.0
        notes = c.get("notes") or []
        if not notes:
            lines.append(f"# {var} clip[{ci}] @{_fmt_float(start)} dur {_fmt_float(dur)}: (empty)")
            continue
        tuples = []
        for n in notes:
            k = int(n.get("key", 60)); st = _fmt_float(n.get("start", 0.0))
            d_ = _fmt_float(n.get("duration", 0.25)); vel = _fmt_float(n.get("velocity", 0.85))
            ch = int(n.get("channel", 0)) & 0xF
            tuples.append(f"({k}, {st}, {d_}, {vel}, {ch})" if ch else f"({k}, {st}, {d_}, {vel})")
        if len(notes) <= 8:
            payload = "[" + ", ".join(tuples) + "]"
        else:
            payload = "[\n        " + ",\n        ".join(tuples) + ",\n    ]"
        lines.append(f"{var}.clips([({_fmt_float(start)}, {_fmt_float(dur)}, {payload})])")

    for a in (t.get("automation") or []):
        param = (a.get("param") or "").lower()
        bps = a.get("breakpoints") or []
        if not bps:
            continue
        tgt = a.get("target") or {}
        kind = tgt.get("kind") or ("volume" if "volume" in param else "pan" if "pan" in param else "unknown")
        if kind == "volume":
            lines.append(f"{var}.automate('volume', [{_norm_bps(bps, _VOL_OFF, _VOL_SCALE)}])")
        elif kind == "pan":
            lines.append(f"{var}.automate('pan', [{_norm_bps(bps, _PAN_OFF, _PAN_SCALE)}])")
        elif kind == "remote":
            di, ri = tgt.get("device_index", 0), tgt.get("remote_index", 0)
            off, scale = tgt.get("value_off"), tgt.get("value_scale")
            lines.append(f"{var}.select_device({di})   # {tgt.get('device', '')}: {tgt.get('param', '')}")
            if scale:
                lines.append(f"{var}.automate('remote', [{_norm_bps(bps, off, scale)}], remote_index={ri})")
            else:
                # target resolved but not calibrated (no live Bitwig at read time):
                # values are RAW/native - they may need scaling to 0..1.
                lines.append(f"# (uncalibrated - values are raw/native, may need scaling to 0..1)")
                lines.append(f"{var}.automate('remote', [{_norm_bps(bps, 0, None)}], remote_index={ri})")
        else:
            # Couldn't resolve which remote it targets (e.g. raw device knob, or a
            # param not on remote page 0). Values preserved - set remote_index + uncomment.
            lines.append(f"# device-param automation ({len(bps)} bps) - target unresolved; set remote_index:")
            lines.append(f"# {var}.automate('remote', [{_norm_bps(bps, 0, None)}], remote_index=0)")

    lines.append("")
    return lines


def _emit_effect_tracks(efx: list[dict]) -> list[str]:
    lines = []
    for ti, t in enumerate(efx):
        nm = (t.get("name") or "").strip() or f"FX{ti}"
        var = f"fx_{ti}_{_pyident(nm, f'fx{ti}').lower()[:24]}"
        lines.append(f"{var} = s.fx_track({nm!r})")
        vol = t.get("volume")
        if vol is not None:
            lines.append(f"{var}.fader({_fmt_float(vol)})")
        pan = _pan_to_signed(t.get("pan", 0.5))
        if abs(pan) > 1e-4:
            lines.append(f"{var}.pan({_fmt_float(pan)})")
        lines.append("")
    return lines


def to_script(data: dict, *, project_label: str = "untitled") -> str:
    """Render `data` (output of `read_project(b, with_clips=True)`) as a script."""
    lines: list[str] = [_emit_header(project_label, data)]
    lines.extend(_emit_song_open(data))
    factory_dir, preset_dirs = _bitwig_dirs()
    preset_idx = _build_preset_index(preset_dirs)
    for t in data.get("tracks", []):
        nm = (t.get("name") or "").strip()
        var = f"t_{t.get('index'):02d}_{_pyident(nm, str(t.get('index'))).lower()[:24]}"
        lines.extend(_emit_track(t, var, factory_dir, preset_idx))
    lines.extend(_emit_effect_tracks(data.get("effect_tracks") or []))
    lines.append("# Master chain not read back - fill in your master devices:")
    lines.append("# s.master(['EQ+', 'Compressor+', 'Peak Limiter'])")
    lines.append("")
    lines.append("# s.play(loop=True)        # uncomment to hear it")
    lines.append("# print(s.render('out.wav'))")
    lines.append("")
    return "\n".join(lines)


__all__ = ["to_script"]
