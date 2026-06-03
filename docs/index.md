# openwig

**Algorithmic composition for Bitwig Studio. Write Python, get songs.**

Goes where Bitwig's official Controller API can't: build arrangements, devices,
modulators, sidechains, automation curves, and full multi-track songs from a
Python program. Render to `.wav`, export to MIDI/JSON.

> Free and open source (GPL-3.0). Windows only. Early beta.

```python
from openwig import Song

s = Song(tempo=128, bars=16, clean=True)

kick = s.track("KICK", device="v9 Kick")
kick.fx("Saturator", Drive=0.20)
kick.clip(s.pulse(36, step=1.0))

bass = s.track("BASS", device="FM-4")
bass.fx("Filter")
bass.clip(s.pulse(33, step=1.0, off=0.5, dur=0.4, vel=0.85))
bass.pump(hi=0.82)

s.master(["EQ+", "Compressor+", "Peak Limiter"])
s.play()
print(s.render("song.wav"))
```

## What you can do

- **Build songs** declaratively - tracks, clips, devices, mix, master chain.
- **Algorithmic composition** - generate notes from Python, not by hand.
- **Modulators + sidechain** - fully programmatic, no GUI dragging.
- **Automation** - offline (no playback needed) or recorded.
- **Render** - to `.wav` via WASAPI loopback (Windows).
- **Export** - MIDI, JSON, project round-trip.

## Where to go next

- [Install](install.md) - get the SDK running in five minutes.
- [Quickstart](quickstart.md) - your first song.

## Compatibility

| openwig | Bitwig Studio | Python |
|------------|---------------|--------|
| 0.1.x      | **6.0.6**     | 3.11+  |

The SDK is **locked** to a specific Bitwig version. Mismatches refuse to
connect with a clear error.
