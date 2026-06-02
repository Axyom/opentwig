"""bitwig_lint.py - sanity checks + assertions over a Song / live project.

Use during composition to catch mistakes early (silent tracks, mute conflicts,
clipping, missing devices, identical clips).
"""
from __future__ import annotations


def lint(song, *, max_volume_warn: float = 0.98, warn_default_names=True):
    """Return a list of (severity, message) issues. Severity is 'warn' or 'error'."""
    issues = []
    snap = song.b.request("state.snapshot")
    track_names = [t.get("name") or "" for t in snap.get("tracks", [])]

    # 1. duplicate track names
    seen = {}
    for n in track_names:
        seen[n] = seen.get(n, 0) + 1
    for n, c in seen.items():
        if c > 1:
            issues.append(("warn", f"track name '{n}' appears {c}x"))

    # 2. default-ish track names (assume Bitwig uses "Audio", "Instrument", "Effect", "Track")
    if warn_default_names:
        for t in snap.get("tracks", []):
            nm = (t.get("name") or "").strip()
            if nm.lower() in {"audio", "instrument", "effect", "track", "", "untitled"}:
                issues.append(("warn", f"track[{t['index']}] has default name {nm!r}"))

    # 3. volume above the warn threshold (likely to clip)
    for t in snap.get("tracks", []):
        v = t.get("volume", 0.0)
        if isinstance(v, (int, float)) and v > max_volume_warn:
            issues.append(("warn", f"track[{t['index']}] {t.get('name')!r} volume={v:.3f} risks clipping"))

    # 4. all-mute / all-solo states
    muted = [t for t in snap.get("tracks", []) if t.get("mute")]
    solos = [t for t in snap.get("tracks", []) if t.get("solo")]
    if snap.get("tracks") and len(muted) == len(snap["tracks"]):
        issues.append(("error", "all tracks muted - nothing will be audible"))
    if len(solos) > 0 and any(t for t in snap.get("tracks", []) if not t.get("solo") and not t.get("mute")):
        issues.append(("warn", f"{len(solos)} track(s) soloed - non-solo tracks silent"))

    # 5. Tracks with no clips (SDK-side; only if Song knows about them)
    for nm, tk in getattr(song, "tracks", {}).items():
        if not getattr(tk, "_clip_specs", []):
            issues.append(("warn", f"track '{nm}' has no clips"))

    # 6. identical clip specs (suggests forgotten variation)
    for nm, tk in getattr(song, "tracks", {}).items():
        specs = [(s, d, tuple(tuple(n) for n in notes)) for (s, d, notes) in getattr(tk, "_clip_specs", [])]
        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                if specs[i][2] == specs[j][2]:
                    issues.append(("warn", f"track '{nm}' clips at {specs[i][0]} and {specs[j][0]} are identical"))
                    break

    return issues


def assert_clean(song, **kwargs):
    """Raise AssertionError if lint() finds any 'error' (warnings OK)."""
    issues = lint(song, **kwargs)
    errs = [m for sev, m in issues if sev == "error"]
    assert not errs, "Project errors:\n  " + "\n  ".join(errs)
    return issues


def print_lint(song, **kwargs):
    issues = lint(song, **kwargs)
    if not issues:
        print("lint: OK"); return
    for sev, m in issues:
        print(f"  [{sev}] {m}")


# ── assertion helpers for tests ─────────────────────────────────────────────

def assert_track_count(song, n):
    snap = song.b.request("state.snapshot")
    got = len(snap.get("tracks", []))
    assert got == n, f"expected {n} tracks, got {got}"


def assert_track(song, name, *, clips=None, notes=None):
    """Assert a track exists with given clip/note counts (SDK-side counts)."""
    assert name in song.tracks, f"missing track {name!r}"
    t = song.tracks[name]
    if clips is not None:
        got = len(t._clip_specs)
        assert got == clips, f"{name!r}: expected {clips} clips, got {got}"
    if notes is not None:
        got = sum(len(n) for _, _, n in t._clip_specs)
        assert got == notes, f"{name!r}: expected {notes} notes, got {got}"


def assert_audible(song, render_path, *, min_rms: float = 1e-3):
    """Render and assert non-silence."""
    from openwig.wire.render import render_to_wav
    r = render_to_wav(song.b, render_path, beats=song.total, tempo=song.tempo)
    assert not r["silent"], f"render at {render_path} was silent (rms={r['rms']})"
    return r
