# Quickstart - your first song

Five minutes from install to a rendered `.wav`.

Before you start: Bitwig Studio must be open, the controller enabled
(Settings -> Controllers -> openwig -> Add -> OpenwigBridge), and `openwig doctor` should
print `compatible`.

A `Note` is `(key, start_beat, duration, velocity)` with named fields and
defaults (`dur=0.5`, `vel=1.0`); raw tuples work too. openwig ships no pattern
generators - you build the lists with ordinary Python, which is the whole point:
anything you can express in a list comprehension, you can play.

## 1. A kick on every beat

```python
from openwig import Song, Note

s = Song(tempo=128, bars=4, clean=True)   # 4 bars = 16 beats

kick = s.track("KICK", device="v9 Kick")
kick.clip([Note(36, beat, dur=0.25) for beat in range(16)])

s.play()
```

`Song(clean=True)` wipes the open project. The list comprehension makes one hit
per beat (MIDI note 36). `.clip(...)` writes them into one arranger clip
spanning the song. You should hear a kick looping at 128 BPM.

## 2. Add a bass

```python
bass = s.track("BASS", device="FM-4")
bass.fx("Filter")
bass.clip([Note(33, beat, dur=0.4, vel=0.85) for beat in range(16)])
```

`.fx("Filter")` chains the Filter device after FM-4. The `0.85` velocity makes
the bass a touch quieter than the kick.

## 3. Closed hats on the off-beats

```python
hats = s.track("HATS", device="v9 Hat Closed")
hats.clip([Note(42, beat + 0.5, dur=0.2, vel=0.6) for beat in range(16)])
```

Adding `0.5` to each start time puts a hat halfway between every kick.

## 4. Side-pump the bass (build it yourself)

There's no `pump` helper - a sidechain-style duck is just a volume automation
curve you write in Python:

```python
duck = []
for beat in range(16):
    duck += [(beat, 0.30), (beat + 0.2, 0.82)]   # drop on the beat, rebound
bass.automate("volume", duck)
```

`automate("volume", points)` writes the breakpoints offline - no routing, no
extra plugin.

## 5. Master chain and render

```python
s.master(["EQ+", "Compressor+", "Peak Limiter"])
print(s.render("first.wav"))
```

`render` stops the transport, plays once from the top, and captures the master
output via WASAPI loopback. It returns a dict with the file `path` plus
`seconds`, `rate`, `channels`, `rms`, and `silent` - so you can confirm it
actually made sound.

## Putting it together

```python
from openwig import Song, Note

s = Song(tempo=128, bars=4, clean=True)

kick = s.track("KICK", device="v9 Kick")
kick.clip([Note(36, beat, dur=0.25) for beat in range(16)])

bass = s.track("BASS", device="FM-4")
bass.fx("Filter")
bass.clip([Note(33, beat, dur=0.4, vel=0.85) for beat in range(16)])

duck = []
for beat in range(16):
    duck += [(beat, 0.30), (beat + 0.2, 0.82)]
bass.automate("volume", duck)

hats = s.track("HATS", device="v9 Hat Closed")
hats.clip([Note(42, beat + 0.5, dur=0.2, vel=0.6) for beat in range(16)])

s.master(["EQ+", "Compressor+", "Peak Limiter"])
print(s.render("first.wav"))
```

## Where to go next

- [API reference](reference.md) - every method.
