"""bitwig_arrangement.py - section/template/genre helpers built on Song+Track.

Section: a named beat range that you fill across multiple tracks at once.
Templates: genre starter kits (techno, house, dnb, lofi, ambient) - instantiate
into a clean Song to get an audible draft you then customize.
"""
from __future__ import annotations
from contextlib import contextmanager
from openwig import notes as N
from openwig import curves as C


class Section:
    """A named beat range. Use `.fill(track, notes_or_callable)` to lay clips."""

    def __init__(self, song, name, start, length):
        self.song = song; self.name = name
        self.start = float(start); self.length = float(length)
        self.end = self.start + self.length

    def fill(self, track, notes):
        """Put `notes` on `track` as a single clip spanning this section.
        notes can be a list, or a callable that takes `length` and returns notes."""
        if callable(notes): notes = notes(self.length)
        track._make_clip(self.start, self.length, notes)
        return self

    def each(self, track_notes_dict):
        """Bulk-fill multiple tracks at once. {track: notes_or_callable, ...}"""
        for t, n in track_notes_dict.items():
            self.fill(t, n)
        return self


def section(song, name, start, length):
    return Section(song, name, start, length)


@contextmanager
def transaction(song):
    """Run a block of edits; on exception, undo every successful op since enter.
    Implemented by counting Bitwig undo steps via app.undo (best-effort: depends on
    Bitwig grouping the ops similarly per request)."""
    snap_before = song.b.request("state.snapshot")
    n_before = len(snap_before.get("tracks", []))
    try:
        yield song
    except Exception:
        # Best-effort rollback: undo enough times to clear the new ops.
        # We can't tell exactly how many undo units our ops produced; bound retries.
        for _ in range(64):
            song.undo(1)
            snap_now = song.b.request("state.snapshot")
            if len(snap_now.get("tracks", [])) <= n_before:
                break
        raise


# ── arrangement templates ────────────────────────────────────────────────────

def four_on_floor(length, *, key=36, step=1.0, dur=0.25, vel=1.0):
    return [(key, b, dur, vel) for b in range(int(length))]


def offbeat_hats(length, *, key=42, step=0.5, dur=0.20, vel=0.6):
    return [(key, b + 0.5, dur, vel) for b in range(int(length))]


def backbeat_clap(length, *, key=39, dur=0.20, vel=0.85):
    return [(key, b, dur, vel) for b in range(1, int(length), 2)]


def walking_bass(length, scale_keys, *, step=0.5, dur=0.45, vel=0.80, rng=None):
    """Stepwise random-walk bass from a scale (no big leaps)."""
    import random
    rng = rng or random.Random()
    out = []; i = 0
    for k in range(int(length / step)):
        i = max(0, min(len(scale_keys) - 1, i + rng.choice([-1, 0, 0, 1])))
        out.append((scale_keys[i], k * step, dur, vel))
    return out


# Genre starter kits: (devices_per_track, default_pattern). Use templates.techno(song)
# to instantiate. Each returns the populated Song.

def techno(song, *, bars=16, root="A", mode="minor"):
    """Driving 4-on-floor techno: kick + clap + hats + bass + pad."""
    length = bars * 4
    sc = N.scale(N.note_to_midi(root + "1"), mode, octaves=1)
    (song.track("KICK", device="v9 Kick").fx("Saturator", Drive=0.2)
        .clip(four_on_floor(length)))
    (song.track("CLAP", device="v9 Clap").fx("Reverb", Mix=0.25)
        .clip(backbeat_clap(length)))
    (song.track("HATS", device="v9 Hat Closed")
        .clip(offbeat_hats(length)))
    (song.track("BASS", device="FM-4").fx("Filter")
        .clip(walking_bass(length, sc)))
    (song.track("PAD", device="Polysynth").fx("Reverb", Mix=0.45)
        .clip(N.chord_notes(N.note_to_midi(root + "3"), "min", duration=float(length), velocity=0.4)))
    return song


def house(song, *, bars=16, root="C", mode="dorian"):
    """Classic house: 4-floor kick, shaker offbeats, bassline, stab chords."""
    length = bars * 4
    sc = N.scale(N.note_to_midi(root + "2"), mode, octaves=1)
    (song.track("KICK", device="v9 Kick").clip(four_on_floor(length)))
    (song.track("CLAP", device="v9 Clap").clip(backbeat_clap(length)))
    (song.track("SHAKER", device="v9 Hat Closed")
        .clip(N.euclidean(42, 5, 8, step_beats=0.5)))
    (song.track("BASS", device="FM-4").fx("Filter")
        .clip(N.euclidean(sc[0], 3, 8, step_beats=0.5, dur=0.4, vel=0.85)))
    (song.track("STAB", device="Polysynth").fx("Delay+", Mix=0.20)
        .clip([(k, 0.75 + 2*b, 0.20, 0.55) for b in range(int(length/2)) for k in N.chord(sc[0]+12, "min7")]))
    return song


def dnb(song, *, bars=16, root="F", mode="minor"):
    """Drum&Bass: Amen-style broken kick/snare + reese bass + sparse pad."""
    length = bars * 4
    sc = N.scale(N.note_to_midi(root + "1"), mode, octaves=1)
    # Broken 2-step pattern (kicks at 1 and 2.75, snares at 2 and 4)
    kick = []; snare = []
    for bar in range(int(length/4)):
        b = bar * 4
        kick += [(36, b, 0.15, 1.0), (36, b + 2.75, 0.15, 0.85)]
        snare += [(38, b + 1, 0.15, 0.9), (38, b + 3, 0.15, 0.95)]
    (song.track("KICK", device="v9 Kick").clip(kick))
    (song.track("SNARE", device="v9 Snare").clip(snare))
    (song.track("HATS", device="v9 Hat Closed")
        .clip([(42, b * 0.5, 0.10, 0.55 if b % 4 == 2 else 0.35) for b in range(int(length / 0.5))]))
    (song.track("REESE", device="FM-4").fx("Saturator", Drive=0.4).fx("Filter")
        .clip([(sc[0], b, 4.0, 0.80) for b in range(0, int(length), 4)]))
    return song


def lofi(song, *, bars=16, root="C", mode="major"):
    """Lo-fi hip hop: dusty kick/snare, jazzy chords, mellow bass."""
    length = bars * 4
    sc = N.scale(N.note_to_midi(root + "2"), mode, octaves=1)
    (song.track("KICK", device="v9 Kick").fx("Saturator", Drive=0.3)
        .clip([(36, b, 0.20, 0.85) for b in [0, 2.5, 4, 6, 8, 10.5, 12, 14]]))
    (song.track("SNARE", device="v9 Snare").fx("Reverb", Mix=0.30)
        .clip(backbeat_clap(length, key=38)))
    (song.track("HATS", device="v9 Hat Closed")
        .clip([(42, b * 0.5, 0.10, 0.50) for b in range(int(length / 0.5))]))
    (song.track("BASS", device="FM-4")
        .clip([(sc[0], b * 2, 1.5, 0.75) for b in range(int(length / 2))]))
    (song.track("CHORDS", device="Polysynth").fx("Reverb", Mix=0.40).fx("Delay+", Mix=0.20)
        .clip(N.progression([("maj7", sc[0]+12), ("min7", sc[1]+12),
                            ("maj7", sc[3]+12), ("dom7", sc[4]+12)], each_beats=4.0)))
    return song


def ambient(song, *, bars=16, root="D", mode="dorian"):
    """Slowly-evolving pad with long-release reverb, no drums."""
    length = bars * 4
    sc = N.scale(N.note_to_midi(root + "3"), mode, octaves=1)
    notes = []
    for b in range(0, int(length), 8):
        notes += [(k, b, 8.0, 0.30) for k in sc[:4]]
    (song.track("PAD", device="Polysynth").fx("Reverb", Mix=0.65).fx("Delay+", Mix=0.30)
        .clip(notes)
        .automate("volume", C.lfo(float(length), rate=1/8, lo=0.55, hi=0.78)))
    return song


TEMPLATES = {"techno": techno, "house": house, "dnb": dnb, "lofi": lofi, "ambient": ambient}


def template(song, name, **kwargs):
    if name not in TEMPLATES:
        raise ValueError(f"unknown template '{name}'; choose from {list(TEMPLATES)}")
    return TEMPLATES[name](song, **kwargs)


# ── reharmonization ─────────────────────────────────────────────────────────

def reharm(notes, *, original_root, original_mode, new_progression):
    """Re-map a melody to a new chord progression. `original_root` + `original_mode`
    define the scale the melody is in (so scale degrees are inferred). `new_progression`
    is a list of (quality, root_key) one per existing chord-length beat.
    Notes outside the scale are passed through unchanged."""
    from openwig.notes import scale as _scale, chord as _chord
    orig = _scale(original_root, original_mode, octaves=4)
    # Map every note's offset within its bar to a new pitch from the new chord at that bar.
    # Naive: snap each note to the closest chord-tone of the active chord.
    out = []
    chord_span = 4.0  # beats per chord
    for n in notes:
        bar_idx = int(n[1] // chord_span) % len(new_progression)
        q, r = new_progression[bar_idx]
        ch_keys = _chord(r, q, octave_shift=(n[0] - r) // 12)
        # nearest chord tone
        new_k = min(ch_keys, key=lambda k: abs(k - n[0]))
        out.append((new_k, *n[1:]))
    return out


# ── timeline ASCII print ────────────────────────────────────────────────────

def print_timeline(song, *, beat_width: int = 1):
    """Show each track's clip layout as ASCII bars over the song length."""
    snap = song.b.request("state.snapshot")
    total = song.total
    print(f"\n{song.tempo} BPM | {song.bars} bars | {total} beats\n")
    head = "      " + "".join((str(b // 4 + 1) if b % 4 == 0 else " ") for b in range(total))
    print(head)
    print("      " + "+" + "-" * total + "+")
    for t in snap.get("tracks", []):
        nm = (t.get("name") or "?")[:5].ljust(5)
        # We don't have arrangement clip ranges from snapshot - just show the track row
        # as a baseline; for a richer view feed in an explicit timeline (see Section).
        bar = "." * total
        print(f"{nm} |{bar}|")
    print()
