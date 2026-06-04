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
import re
from typing import Any

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


def _emit_track(t: dict, var: str) -> list[str]:
    lines: list[str] = []
    name = (t.get("name") or "").strip() or f"track_{t.get('index')}"
    kind = _classify_track(t)
    devs = t.get("devices") or []

    if kind == "audio":
        lines.append(f"{var} = s.audio_track({name!r})")
    else:
        first_dev = devs[0] if devs else None
        if first_dev:
            lines.append(f"{var} = s.track({name!r}, device={first_dev['name']!r})")
        else:
            lines.append(f"{var} = s.track({name!r})")

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
        kw_parts = []
        for r in [r for r in (d.get("remotes") or []) if r.get("name")][:4]:
            val = r.get("value")
            if not isinstance(val, (int, float)):
                continue
            kw_parts.append(f"{_pyident(r['name'], 'p')}={_fmt_float(val)}")
        lines.append(f"{var}.fx({dname!r}" + ((", " + ", ".join(kw_parts)) if kw_parts else "") + ")")

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
        param = a.get("param") or ""
        bps = a.get("breakpoints") or []
        if not bps:
            continue
        if "volume" in param.lower():
            sdk_param = "volume"
        elif "pan" in param.lower():
            sdk_param = "pan"
        else:
            lines.append(f"# TODO automation: {param!r} ({len(bps)} bps) - resolve param name")
            continue
        bp_lines = ", ".join(f"({_fmt_float(b.get('time'))}, {_fmt_float(b.get('value'))})" for b in bps[:512])
        lines.append(f"{var}.automate({sdk_param!r}, [{bp_lines}])")

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
    for t in data.get("tracks", []):
        nm = (t.get("name") or "").strip()
        var = f"t_{t.get('index'):02d}_{_pyident(nm, str(t.get('index'))).lower()[:24]}"
        lines.extend(_emit_track(t, var))
    lines.extend(_emit_effect_tracks(data.get("effect_tracks") or []))
    lines.append("# Master chain not read back - fill in your master devices:")
    lines.append("# s.master(['EQ+', 'Compressor+', 'Peak Limiter'])")
    lines.append("")
    lines.append("# s.play(loop=True)        # uncomment to hear it")
    lines.append("# print(s.render('out.wav'))")
    lines.append("")
    return "\n".join(lines)


__all__ = ["to_script"]
