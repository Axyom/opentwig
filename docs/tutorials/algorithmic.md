# Algorithmic composition

This is the tutorial that justifies the SDK. We'll build an eight-bar piece
where the notes are *generated* - Euclidean drums, a scale-constrained walking
bass, an arpeggio over a chord progression - and the filter cutoff evolves
across the arrangement via a curve. Anything you'd be clicking on a
piano-roll a thousand times for, you do in Python in a handful of lines.

## What "algorithmic" means here

A drum pattern is just a list of `(note, start, duration, velocity)` tuples.
A bassline is a function from chord and tempo to a list. An arrangement is a
function from sections to clips. The SDK gives you the I/O - Bitwig + bridge +
clip insertion + automation - and a small library of note + curve primitives.
You supply the algorithm.

## Primitives at a glance

```python
from openwig import notes as N, curves as C

# Notes - every helper returns a list of (key, start_beat, dur_beats, velocity).
N.euclidean(38, 5, 16)                              # 5 hits spread across 16 sixteenth-note steps
N.scale("A", "minor")                              # MIDI keys for A natural minor
N.markov([60, 62, 64, 67], 32)                     # 32-step Markov walk through those keys
N.arp([57, 60, 64], "up", step=0.25, length=16)    # ascending arpeggio, 16 steps
N.chord_notes("A3", "min", duration=4.0)           # held A minor chord
N.humanize(some_notes, time=0.02, vel=0.05)        # timing + velocity jitter
N.quantize(some_notes, grid=0.125)                 # snap to 32nd-note grid

# s.pulse() is on Song (not the notes module):
# s.pulse(36, step=1.0)   ->  one hit per beat for the whole song length

# Curves - every helper returns (beat, value) breakpoints for Track.automate().
C.lfo(8, shape="sine", rate=0.25, lo=0.2, hi=0.8)
C.ramp(0.2, 0.9, end_beat=8)
C.env_adsr(4, attack=0.05, decay=0.3, sustain=0.6, release=0.4)
C.sample_hold(8, rate=1.0, lo=0.3, hi=0.7, seed=42)
```

The full list is in the [API reference](../reference.md).

## The piece

We'll build:

- a **Euclidean kick** (5-against-16 cross-rhythm),
- a **scale-locked bass** walking through A minor pentatonic,
- an **arpeggio** over a four-chord progression on a pad,
- and a **slow volume swell** on the bass that lasts the whole eight bars.

```python
import random
from openwig import Song, notes as N, curves as C

random.seed(7)
s = Song(tempo=124, bars=8, clean=True)

# ── Drums: Euclidean kick + offbeat hat ─────────────────────────────────────
# euclidean() generates one bar; repeat it across all 8 bars.
kick_bar = N.euclidean(36, 5, 16, step_beats=0.25)
kick_pattern = N.repeat(kick_bar, s.bars, length=4.0)

kick = s.track("KICK", device="v9 Kick")
kick.clip(kick_pattern)

hat = s.track("HAT", device="v9 Hat Closed")
hat.clip(s.pulse(42, step=0.5, off=0.25, vel=0.55))

# ── Bass: Markov walk over A minor pentatonic, humanized ───────────────────
am_pent = N.scale("A", "minor_pent", octaves=2)
# 128 steps at 0.25 beats/step = 32 beats = 8 bars
walk = N.markov(am_pent, 128, step=0.25, dur=0.22, vel=0.8)

bass = s.track("BASS", device="FM-4")
bass.fx("Filter")
bass.clip(N.humanize(walk, time=0.03, vel=0.04))

# Slow volume swell over the whole 8 bars.
bass.automate("volume", C.ramp(0.4, 1.0, end_beat=s.total))

# ── Pad: arpeggiate a four-chord loop ──────────────────────────────────────
PROG = [("min", "A3"), ("maj", "F3"), ("maj", "C3"), ("maj", "G3")]

pad = s.track("PAD", device="Polysynth")
pad.fx("Reverb")

for i, (quality, root) in enumerate(PROG):
    keys = N.chord(root, quality)
    # 32 steps * 0.25 = 8 beats = 2 bars per chord
    arp_notes = N.arp(keys, "up", step=0.25, dur=0.22, vel=0.5, length=32)
    pad.clip(arp_notes, dur=8, start=i * 8)

# ── Master + render ─────────────────────────────────────────────────────────
s.master(["EQ+", "Compressor+", "Peak Limiter"])
print(s.render("algorithmic.wav"))
```

## What just happened

- **`N.euclidean(36, 5, 16, step_beats=0.25)`** spread five kick hits as
  evenly as possible across 16 sixteenth-notes (one bar). `N.repeat(..., 8,
  length=4.0)` tiled it across all eight bars. That's a 5-against-16
  cross-rhythm - a single line of Python for something nobody draws by hand.
- **`N.markov(am_pent, 128, step=0.25)`** is a one-step Markov walk. Each
  next note is chosen at random from `am_pent` (the A minor pentatonic). Same
  seed gives the same notes every time.
- **`N.humanize(..., time=0.03, vel=0.04)`** added up to 30ms of timing
  jitter and 4% velocity jitter. Identical pattern, less robotic.
- **`bass.automate("volume", C.ramp(...))`** wrote a straight volume ramp
  directly onto the arranger lane - no transport recording, no GUI. The bass
  opens up from 40% to full over the 8 bars.
- **`N.arp(keys, "up", step=0.25, length=32)`** generated a 2-bar ascending
  arpeggio per chord. Looping four chords * 2 bars = 8-bar arrangement.

## Variations to try

```python
# Same algorithm, different seed - genuinely new variation
random.seed(13)
```

```python
# Descending arpeggio on the pad
arp_notes = N.arp(keys, "down", step=0.25, dur=0.22, vel=0.5, length=32)
```

```python
# Sine LFO on volume instead of a ramp
bass.automate("volume", C.lfo(s.total, shape="sine", rate=0.25, lo=0.6, hi=1.0))
```

```python
# Sidechain the pad to the kick - see the sidechain tutorial
pad.sidechain_from(kick, sink_device_index=1)
```

## Why this is the SDK's point

Two things you couldn't do in Bitwig's GUI in the same time:

1. **Re-generate the whole bassline with one seed change.** A seed is one
   character. To regenerate by hand you'd repaint 128 notes.
2. **Write volume automation over 8 bars from a `ramp()` curve, no transport.**
   The SDK puts the breakpoints in directly without recording.

This is also why the SDK is locked to a Bitwig major version - those operations
reach into private internals.

## Next

- [Sidechain pump](sidechain.md) - duck a track on another track's signal.
- [Render to wav](render.md) - how to capture the output to a file.
- [Cookbook](../cookbook.md) - short recipes per task.
