# Quickstart - your first song

Five minutes from install to a rendered `.wav`.

Before you start: Bitwig Studio must be open, the controller enabled
(Settings -> Controllers -> openwig -> Add -> OpenwigBridge), and `openwig doctor` should
print `compatible`.

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
bass = s.track("BASS", device="Polysynth")
bass.fx("Filter")
bass.clip([Note(33, beat + 0.5, dur=0.4, vel=0.85) for beat in range(16)])
```

`.fx("Filter")` chains the Filter device after Polysynth. `beat + 0.5` puts the bass
on the off-beat, and `vel=0.85` keeps it a touch quieter than the kick.

## 3. Closed hats on the off-beats

```python
hats = s.track("HATS", device="v9 Hat Closed")
hats.clip([Note(42, beat + 0.5, dur=0.2, vel=0.6) for beat in range(16)])
```

Adding `0.5` to each start time puts a hat halfway between every kick.

## 4. Side-pump the bass

```python
duck = []
for beat in range(16):
    duck += [(beat, 0), (beat + 0.99, 1)]
bass.automate("volume", duck)
```

`automate("volume", points)` writes the breakpoints offline.

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

bass = s.track("BASS", device="Polysynth")
bass.fx("Filter")
bass.clip([Note(33, beat+0.5, dur=0.4, vel=0.85) for beat in range(16)])

duck = []
for beat in range(16):
    duck += [(beat, 0), (beat + 0.99, 1)]
bass.automate("volume", duck)

hats = s.track("HATS", device="v9 Hat Closed")
hats.clip([Note(42, beat + 0.5, dur=0.2, vel=0.6) for beat in range(16)])

s.master(["EQ+", "Compressor+", "Peak Limiter"])
print(s.render("first.wav"))
```

## Where to go next

- [API reference](reference.md) - every method.
