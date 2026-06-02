# Compatibility

## Supported Bitwig versions

| openwig version | Bitwig Studio version | Status   |
|--------------------|-----------------------|----------|
| 0.1.x              | **6.x**               | Current  |

This SDK is **locked** to one Bitwig version at a time. The bridge handshake
checks the live Bitwig version on connect and raises
`IncompatibleBitwigVersion` if it doesn't match.

## Why the lock?

The SDK reaches into Bitwig's private, internal Java classes by reflection to
implement features the public Controller API doesn't expose (modulator insert,
sidechain wiring, offline automation, arranger audio-clip insert, ...). Those
private symbols can be renamed or removed in any Bitwig point release - there
is no API stability promise on them, because they were never public to begin
with.

Rather than silently misbehave on a new Bitwig version, the SDK fails fast:

```
IncompatibleBitwigVersion: Bitwig Studio '7.0.0' is not supported by this
version of openwig. This SDK supports: 6.x. Either install a matching
Bitwig version or upgrade openwig.
```

## Upgrading

When a new Bitwig version comes out, you'll need a matching SDK release. The
SDK's `CHANGELOG.md` records which Bitwig version each release targets.

## Bypass (advanced, not recommended)

For experimentation only, you can bypass the version check:

```python
from openwig import Song

s = Song(tempo=128, bars=8, check_version=False)   # may crash unpredictably
```

You're on your own past this point - most SDK methods will still appear to
work but some will silently no-op or crash Bitwig.

## Python

Python 3.11 or newer. Tested on 3.11 and 3.12.

## Operating system

Windows only. Render-to-wav uses WASAPI loopback (a Windows-only API).
