# openwig

**Algorithmic composition for Bitwig Studio. Write Python, get songs.**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Bitwig 6.0.6](https://img.shields.io/badge/Bitwig-6.0.6-orange.svg)](#compatibility)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](#install)

Goes where Bitwig's official Controller API can't: build arrangements, devices,
modulators, sidechains, automation curves, and full multi-track songs from a
Python program. Render to `.wav`, export to MIDI/JSON.

Free and open source (GPL-3.0). Windows only. Early beta.

**[Documentation →](https://axyom.github.io/openwig/)**

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

## Compatibility

| openwig | Bitwig Studio | Python | OS |
|------------|---------------|--------|----|
| 0.1.x      | **6.0.6**     | 3.11+  | Windows |

The SDK reaches into Bitwig internals that move across releases, so it targets
a specific Bitwig version. A mismatch refuses to connect with a clear error-
bump the SDK when you bump Bitwig. Rendering uses WASAPI loopback (Windows).

## Install

```bash
pip install openwig
python -m openwig install   # copies the controller into Bitwig's user dir
```

Then in Bitwig: **Settings → Controllers → Add → OpenwigBridge** (one time), and verify with `python -m openwig doctor`.

Full guide (requirements, troubleshooting, uninstall): **[Install docs →](https://axyom.github.io/openwig/install/)**

## Contributing

Issues and PRs welcome. Currently Windows + Bitwig 6, tested on Bitwig 6.0.6 only.

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
