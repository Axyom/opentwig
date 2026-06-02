"""bitwig_notes.py - pure-data note/pattern/scale helpers (no bridge calls).

Notes are 5-tuples: (key, start_beat, duration, velocity[, channel]).
All functions return new note lists; none mutate the input.
"""
from __future__ import annotations
import math, random
from typing import Iterable, Sequence

Note = tuple
NoteList = list[Note]


# ── pattern generators ───────────────────────────────────────────────────────

def euclidean(key: int, pulses: int, steps: int, *, step_beats: float = 0.25,
              off: float = 0.0, dur: float = 0.20, vel: float = 1.0) -> NoteList:
    """Bjorklund Euclidean rhythm: `pulses` evenly-distributed hits across `steps`
    slots. Classic for techno (3-in-8 cowbell, 5-in-8 cinquillo, 7-in-16 cumbia)."""
    pattern = [False] * steps
    if pulses <= 0: return []
    bucket = 0
    for i in range(steps):
        bucket += pulses
        if bucket >= steps:
            bucket -= steps
            pattern[i] = True
    out = []
    for i, hit in enumerate(pattern):
        if hit:
            out.append((key, off + i * step_beats, dur, vel))
    return out


def markov(seed_notes: Sequence[int], length: int, *, start: float = 0.0,
           step: float = 0.25, dur: float = 0.20, vel: float = 0.85,
           order: int = 1, rng: random.Random | None = None) -> NoteList:
    """Order-N Markov-chain melody from a corpus of MIDI keys. Trains the chain on
    `seed_notes` then generates `length` steps from the resulting transition table.
    Falls back to uniform over seen keys at unseen contexts."""
    rng = rng or random.Random()
    if len(seed_notes) < order + 1:
        # too little data; just repeat the seed
        return [(seed_notes[i % len(seed_notes)], start + i * step, dur, vel) for i in range(length)]
    table: dict[tuple, list[int]] = {}
    for i in range(len(seed_notes) - order):
        ctx = tuple(seed_notes[i:i + order])
        table.setdefault(ctx, []).append(seed_notes[i + order])
    state = tuple(seed_notes[:order])
    out = [(seed_notes[i], start + i * step, dur, vel) for i in range(order)]
    for j in range(order, length):
        nxt = rng.choice(table.get(state, list(set(seed_notes))))
        out.append((nxt, start + j * step, dur, vel))
        state = tuple(list(state[1:]) + [nxt])
    return out


def weighted(choices: Sequence[tuple[int, float]], length: int, *,
             start: float = 0.0, step: float = 0.25, dur: float = 0.20,
             vel: float = 0.85, rng: random.Random | None = None) -> NoteList:
    """Pick `length` notes from a `(key, weight)` list with replacement. Useful for
    biased random sequences (e.g. root weighted heavier than approach notes)."""
    rng = rng or random.Random()
    keys, weights = zip(*choices)
    out = []
    for i in range(length):
        k = rng.choices(keys, weights=weights, k=1)[0]
        out.append((k, start + i * step, dur, vel))
    return out


# ── note-list transforms (all return new lists) ──────────────────────────────

def transpose(notes: Iterable[Note], semitones: int) -> NoteList:
    return [(n[0] + semitones, *n[1:]) for n in notes]


def quantize(notes: Iterable[Note], grid: float = 0.25) -> NoteList:
    """Snap note start times to the nearest `grid` (in beats)."""
    return [(n[0], round(n[1] / grid) * grid, *n[2:]) for n in notes]


def retrograde(notes: Iterable[Note], length: float) -> NoteList:
    """Reverse note order in time (mirror around length/2). Preserves durations."""
    notes = list(notes)
    return [(n[0], max(0.0, length - n[1] - n[2]), *n[2:]) for n in notes]


def invert(notes: Iterable[Note], axis_key: int = 60) -> NoteList:
    """Pitch inversion around `axis_key` (default C4=60). New_key = 2*axis - old."""
    return [(2 * axis_key - n[0], *n[1:]) for n in notes]


def humanize(notes: Iterable[Note], *, time: float = 0.02, vel: float = 0.10,
             rng: random.Random | None = None) -> NoteList:
    """Jitter start time (±`time` beats) and velocity (±`vel`, clamped to [0,1])."""
    rng = rng or random.Random()
    out = []
    for n in notes:
        t = max(0.0, n[1] + rng.uniform(-time, time))
        v = max(0.05, min(1.0, n[3] + rng.uniform(-vel, vel)))
        out.append((n[0], t, n[2], v, *n[4:]))
    return out


def swing(notes: Iterable[Note], amount: float = 0.15, grid: float = 0.5) -> NoteList:
    """Push every off-beat by `amount * grid` (typical 0.10–0.20). Standard "swung
    eighths" feel at grid=0.5, amount=0.16."""
    out = []
    for n in notes:
        slot = round(n[1] / grid)
        delay = amount * grid if (slot % 2 == 1) else 0.0
        out.append((n[0], n[1] + delay, *n[2:]))
    return out


def stretch(notes: Iterable[Note], factor: float) -> NoteList:
    """Time-scale: multiply start and duration by `factor` (2.0 = half-time)."""
    return [(n[0], n[1] * factor, n[2] * factor, *n[3:]) for n in notes]


def repeat(notes: Iterable[Note], times: int, gap: float = 0.0,
           length: float | None = None) -> NoteList:
    """Tile a note list `times` over; `length` = block length (default = max end+gap)."""
    notes = list(notes)
    if length is None:
        length = max((n[1] + n[2] for n in notes), default=0.0) + gap
    out = []
    for k in range(times):
        offset = k * length
        for n in notes:
            out.append((n[0], n[1] + offset, *n[2:]))
    return out


def shift(notes: Iterable[Note], beats: float) -> NoteList:
    """Time-shift notes by `beats` (positive = later); drops notes that would land < 0."""
    return [(n[0], n[1] + beats, *n[2:]) for n in notes if n[1] + beats >= 0]


def velocity_scale(notes: Iterable[Note], factor: float) -> NoteList:
    return [(n[0], n[1], n[2], max(0.05, min(1.0, n[3] * factor)), *n[4:]) for n in notes]


def merge(*note_lists: Iterable[Note]) -> NoteList:
    """Layer note lists (concat, time-sorted)."""
    out = []
    for lst in note_lists:
        out.extend(lst)
    out.sort(key=lambda n: n[1])
    return out


# ── scales / chords / arps ───────────────────────────────────────────────────

SCALES = {
    "major":      [0, 2, 4, 5, 7, 9, 11],
    "minor":      [0, 2, 3, 5, 7, 8, 10],   # natural minor
    "harmonic":   [0, 2, 3, 5, 7, 8, 11],   # harmonic minor
    "melodic":    [0, 2, 3, 5, 7, 9, 11],   # melodic minor (ascending)
    "dorian":     [0, 2, 3, 5, 7, 9, 10],
    "phrygian":   [0, 1, 3, 5, 7, 8, 10],
    "lydian":     [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian":    [0, 1, 3, 5, 6, 8, 10],
    "pentatonic": [0, 2, 4, 7, 9],
    "minor_pent": [0, 3, 5, 7, 10],
    "blues":      [0, 3, 5, 6, 7, 10],
    "chromatic":  list(range(12)),
}

CHORDS = {
    "maj":     [0, 4, 7],
    "min":     [0, 3, 7],
    "dim":     [0, 3, 6],
    "aug":     [0, 4, 8],
    "sus2":    [0, 2, 7],
    "sus4":    [0, 5, 7],
    "maj7":    [0, 4, 7, 11],
    "min7":    [0, 3, 7, 10],
    "dom7":    [0, 4, 7, 10],
    "min7b5":  [0, 3, 6, 10],
    "dim7":    [0, 3, 6, 9],
    "add9":    [0, 4, 7, 14],
    "min9":    [0, 3, 7, 10, 14],
    "maj9":    [0, 4, 7, 11, 14],
}

NOTE_NAMES = {n: i for i, n in enumerate(["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"])}
NOTE_NAMES |= {"Db":1,"Eb":3,"Gb":6,"Ab":8,"Bb":10}


def note_to_midi(name: str) -> int:
    """'C4' -> 60, 'F#3' -> 54, 'Bb5' -> 82. Octave optional (default 4)."""
    i = 0
    while i < len(name) and (name[i].isalpha() or name[i] in "#b"): i += 1
    pitch, octave = name[:i], name[i:] or "4"
    return NOTE_NAMES[pitch] + (int(octave) + 1) * 12


def scale(root: int | str, mode: str = "major", octaves: int = 2) -> list[int]:
    """All MIDI keys in `mode` from `root` over `octaves`."""
    if isinstance(root, str): root = note_to_midi(root)
    steps = SCALES[mode]
    return [root + 12 * o + s for o in range(octaves) for s in steps]


def chord(root: int | str, quality: str = "maj", octave_shift: int = 0) -> list[int]:
    """MIDI keys of a chord. quality is from CHORDS (maj, min, maj7, dom7, ...)."""
    if isinstance(root, str): root = note_to_midi(root)
    return [root + 12 * octave_shift + iv for iv in CHORDS[quality]]


def chord_notes(root: int | str, quality: str, *, start: float = 0.0,
                duration: float = 4.0, velocity: float = 0.6) -> NoteList:
    """A held chord (all notes start together, same duration)."""
    return [(k, start, duration, velocity) for k in chord(root, quality)]


def arp(keys: Sequence[int], pattern: str = "up", *, start: float = 0.0,
        step: float = 0.25, length: int | None = None, dur: float = 0.20,
        vel: float = 0.85) -> NoteList:
    """Arpeggiate `keys` in `pattern` for `length` steps.
    pattern: 'up' | 'down' | 'updown' | 'random'."""
    keys = list(keys)
    if not keys: return []
    length = length or len(keys)
    if pattern == "down":   seq = list(reversed(keys))
    elif pattern == "updown":
        seq = keys + list(reversed(keys[1:-1]))
    elif pattern == "random":
        rng = random.Random(); seq = [rng.choice(keys) for _ in range(length)]
    else:                    seq = keys
    out = []
    for i in range(length):
        k = seq[i % len(seq)] if pattern != "random" else seq[i]
        out.append((k, start + i * step, dur, vel))
    return out


def progression(qualities_in_root: Sequence[tuple[str, int]], *,
                each_beats: float = 4.0, velocity: float = 0.55) -> NoteList:
    """Lay out a chord progression as held chords. Input: [(quality, root_key), ...]
    e.g. [('min', 57), ('maj', 65), ('maj', 60), ('maj', 55)] = Am F C G."""
    out = []
    for i, (q, r) in enumerate(qualities_in_root):
        out += chord_notes(r, q, start=i * each_beats, duration=each_beats, velocity=velocity)
    return out


# ── ASCII piano-roll for debugging / printing ───────────────────────────────

def ascii_roll(notes: Iterable[Note], *, length: float = 16.0, grid: float = 0.25,
               low: int = 36, high: int = 84) -> str:
    """Render a piano-roll string. low/high cap the key range; grid is step width."""
    notes = list(notes)
    if not notes: return "(empty)"
    cols = int(math.ceil(length / grid))
    rows = high - low + 1
    canvas = [["." for _ in range(cols)] for _ in range(rows)]
    for n in notes:
        key, st, du = n[0], n[1], n[2]
        if key < low or key > high: continue
        r = (high - key)
        c0 = int(st / grid); c1 = max(c0 + 1, int((st + du) / grid))
        for c in range(c0, min(c1, cols)):
            canvas[r][c] = "#" if c == c0 else "-"
    lines = []
    name_of = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
    for r, row in enumerate(canvas):
        k = high - r
        lbl = f"{name_of[k % 12]:>2}{k // 12 - 1}"
        lines.append(f"{lbl} |{''.join(row)}|")
    return "\n".join(lines)
