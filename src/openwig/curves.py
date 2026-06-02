"""bitwig_curves.py - synthesize automation breakpoint lists.

All functions return `[(beat, value), ...]` or `[(beat, value, curvature, "hold")]`
compatible with `Song.automate()` / `Track.automate()`. Values are normalized [0,1].
"""
from __future__ import annotations
import math, random
from typing import Sequence


def lfo(span: float, *, shape: str = "sine", rate: float = 1.0, lo: float = 0.0,
        hi: float = 1.0, phase: float = 0.0, samples_per_cycle: int = 16) -> list[tuple]:
    """Continuous LFO over `span` beats. shape: 'sine' | 'tri' | 'saw' | 'square' | 'ramp'.
    `rate` = cycles per beat. `samples_per_cycle` = breakpoint density per cycle."""
    out = []
    total_samples = max(2, int(span * rate * samples_per_cycle))
    for i in range(total_samples + 1):
        t = i / (rate * samples_per_cycle)        # beat position
        if t > span: t = span
        x = (t * rate + phase) % 1.0              # 0..1 cycle phase
        if shape == "sine":   v = 0.5 + 0.5 * math.sin(2 * math.pi * x)
        elif shape == "tri":  v = 1 - 2 * abs(x - 0.5)
        elif shape == "saw":  v = x
        elif shape == "ramp": v = 1 - x
        elif shape == "square": v = 1.0 if x < 0.5 else 0.0
        else: raise ValueError(f"unknown shape: {shape}")
        out.append((t, lo + (hi - lo) * v))
        if t >= span: break
    return out


def sample_hold(span: float, *, rate: float = 1.0, lo: float = 0.0, hi: float = 1.0,
                seed: int | None = None) -> list[tuple]:
    """Stepped random ('S&H') - new random value every 1/rate beats, held until next."""
    rng = random.Random(seed)
    pts = []
    t = 0.0; step = 1.0 / rate
    while t < span:
        v = lo + (hi - lo) * rng.random()
        pts.append((t, v, 0.0, "hold"))
        t += step
    pts.append((span, pts[-1][1], 0.0, "hold"))
    return pts


def ramp(start_val: float, end_val: float, *, start_beat: float = 0.0,
         end_beat: float = 4.0) -> list[tuple]:
    """Single straight ramp."""
    return [(start_beat, start_val), (end_beat, end_val)]


def env_adsr(span: float, *, attack: float = 0.05, decay: float = 0.10,
             sustain: float = 0.7, release: float = 0.2, peak: float = 1.0) -> list[tuple]:
    """ADSR-shaped curve as breakpoints (beat units). Returns peak at the attack
    apex, sustains, releases to 0 at span."""
    a = max(0.0, attack)
    d = max(0.0, decay)
    r = max(0.0, release)
    if a + d + r > span:                     # squash if too long
        f = span / max(0.001, a + d + r); a *= f; d *= f; r *= f
    pts = [(0.0, 0.0)]
    pts.append((a, peak))
    pts.append((a + d, sustain))
    pts.append((span - r, sustain))
    pts.append((span, 0.0))
    return pts


def gate(active_ranges: Sequence[tuple[float, float]], *, off: float = 0.0,
         on: float = 1.0, span: float = 0.0) -> list[tuple]:
    """Step between off/on across explicit ranges. Useful for "play only here" gates."""
    pts = [(0.0, off, 0.0, "hold")]
    for s, e in active_ranges:
        pts.append((s, on, 0.0, "hold"))
        pts.append((e, off, 0.0, "hold"))
    if span > 0 and (not pts or pts[-1][0] < span):
        pts.append((span, off, 0.0, "hold"))
    return pts


def follow_rms(wav_path: str, *, bin_beats: float = 0.10, tempo: float = 120.0,
               lo: float = 0.0, hi: float = 1.0, invert: bool = False,
               smoothing: int = 4) -> list[tuple]:
    """Envelope follower: read RMS from a rendered .wav and emit per-bin points
    suitable for driving any param (volume duck, filter sweep). `bin_beats` = window
    size. `invert` for ducking (loud source -> low output)."""
    import wave, struct
    with wave.open(wav_path, "rb") as w:
        ch = w.getnchannels(); sr = w.getframerate(); n = w.getnframes()
        raw = w.readframes(n)
    fmt = "<" + "h" * (n * ch)
    samples = struct.unpack(fmt, raw)
    mono = [(samples[i] + (samples[i+1] if ch == 2 else samples[i])) / (2.0 if ch == 2 else 1.0) for i in range(0, n*ch, ch)]
    secs_per_beat = 60.0 / tempo
    bin_samples = max(1, int(bin_beats * secs_per_beat * sr))
    rms = []
    for i in range(0, len(mono), bin_samples):
        block = mono[i:i + bin_samples]
        if not block: continue
        s = sum(x * x for x in block) / len(block)
        rms.append(math.sqrt(s) / 32768.0)
    if smoothing > 1:                    # moving-average smoothing
        sm = []
        for i in range(len(rms)):
            lo_i = max(0, i - smoothing // 2); hi_i = min(len(rms), i + smoothing // 2 + 1)
            sm.append(sum(rms[lo_i:hi_i]) / (hi_i - lo_i))
        rms = sm
    peak = max(rms) or 1.0
    out = []
    for i, v in enumerate(rms):
        n = v / peak
        if invert: n = 1.0 - n
        out.append((i * bin_beats, lo + (hi - lo) * n))
    return out


def sidechain_duck(span: float, *, every: float = 1.0, recover: float = 0.85,
                   hi: float = 0.82, duck: float = 0.30) -> list[tuple]:
    """Per-beat sidechain pump curve (use w/ track.automate('volume', ...)). Same
    as Track.pump() but standalone (composable with other ops)."""
    pts = []
    t = 0.0
    while t < span:
        pts.append((t, duck))
        pts.append((t + recover, hi))
        t += every
    return pts
