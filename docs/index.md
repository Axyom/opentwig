# openwig

**Algorithmic composition for Bitwig Studio. Write Python, get songs.**

Goes where Bitwig's official Controller API can't: build arrangements, devices, automation, and full multi-track songs from a Python
program, then render to `.wav`.

> Free and open source (GPL-3.0). Windows only. Early alpha.

```python
from openwig import Song, Note

s = Song(tempo=128, bars=4, clean=True)

kick = s.track("KICK", device="v9 Kick")
kick.clip([Note(36, beat, dur=0.25) for beat in range(16)])             # four-on-the-floor

bass = s.track("BASS", device="Polysynth")
bass.fx("Filter")
bass.clip([Note(33, beat+0.5, dur=0.4, vel=0.85) for beat in range(16)])  # offbeat root

s.master(["EQ+", "Compressor+", "Peak Limiter"])
s.play()
print(s.render("song.wav"))
```

## What you can do

- **Build songs** - tracks, clips, devices, mix, master chain.
- **Automation**
- **Render** - to `.wav` via WASAPI loopback (Windows).

## Where to go next

- [Install](install.md) - get the SDK running in five minutes.
- [Quickstart](quickstart.md) - your first song.

## Compatibility

| openwig | Bitwig Studio | Python | OS |
|------------|---------------|--------|----|
| 0.1.x      | **6.0.x**     | 3.11+  | Windows |
