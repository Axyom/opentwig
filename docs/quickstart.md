# Quickstart - your first song

Five minutes from install to a rendered `.wav`.

Before you start: Bitwig Studio must be open, the controller enabled
(Settings -> Controllers -> Add -> OpenwigBridge), and `openwig doctor` should
print `compatible`.

## 1. A kick on every beat

```python
from openwig import Song

s = Song(tempo=128, bars=4, clean=True)

kick = s.track("KICK", device="v9 Kick")
kick.clip(s.pulse(36, step=1.0))

s.play()
```

`Song(clean=True)` wipes the open project. `s.pulse(36, step=1.0)` generates
one hit per beat (MIDI note 36). `.clip(...)` writes them into one arranger
clip spanning the song. You should hear a kick looping at 128 BPM.

## 2. Add a bass

```python
bass = s.track("BASS", device="FM-4")
bass.fx("Filter")
bass.clip(s.pulse(33, step=1.0, dur=0.4, vel=0.85))
```

`.fx("Filter")` chains the Filter device after FM-4. `vel=0.85` makes the
bass a touch quieter than the kick.

## 3. Closed hats on the off-beats

```python
hats = s.track("HATS", device="v9 Hat Closed")
hats.clip(s.pulse(42, step=0.5, off=0.25, vel=0.6))
```

`step=0.5` = one hat every half-beat. `off=0.25` shifts the pattern a quarter
beat so hats fall between kicks.

## 4. Side-pump the bass

```python
bass.pump(hi=0.82)
```

Volume drops to 0.82 on each kick hit and rebounds - sidechain duck without
wiring the routing yourself.

## 5. Master chain and render

```python
s.master(["EQ+", "Compressor+", "Peak Limiter"])
print(s.render("first.wav"))
```

`render` stops the transport, plays once from the top, and captures the master
output via WASAPI loopback. Returns the absolute path to the file.

## Putting it together

```python
from openwig import Song

s = Song(tempo=128, bars=4, clean=True)

kick = s.track("KICK", device="v9 Kick")
kick.clip(s.pulse(36, step=1.0))

bass = s.track("BASS", device="FM-4")
bass.fx("Filter")
bass.clip(s.pulse(33, step=1.0, dur=0.4, vel=0.85))
bass.pump(hi=0.82)

hats = s.track("HATS", device="v9 Hat Closed")
hats.clip(s.pulse(42, step=0.5, off=0.25, vel=0.6))

s.master(["EQ+", "Compressor+", "Peak Limiter"])
print(s.render("first.wav"))
```

## Where to go next

- [Algorithmic composition](tutorials/algorithmic.md) - generate notes from
  rules instead of writing them by hand.
- [Sidechain pump](tutorials/sidechain.md) - more pump options.
- [Render to wav](tutorials/render.md) - stems, loopback pitfalls.
- [Cookbook](cookbook.md) - short recipes for common tasks.
- [API reference](reference.md) - every method.
