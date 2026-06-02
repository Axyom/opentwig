# Cookbook

Short recipes for common tasks. Each one is copy-paste runnable against a
fresh Bitwig project.

## Four-on-the-floor

```python
from openwig import Song

s = Song(tempo=128, bars=4, clean=True)
s.track("KICK", device="v9 Kick").clip(s.pulse(36, step=1.0))
s.play()
```

## Held bass note for 4 bars

```python
s.track("BASS", device="FM-4").clip([(33, 0.0, s.total, 0.85)])
```

## Modulator on a synth parameter

```python
t = s.track("LEAD", device="Polysynth")
t.add_modulator("LFO")
t.map_modulator(source_index=0, dest="remote", remote_index=0, amount=0.8)
```

## Sidechain bass-from-kick

```python
s.tracks["BASS"].sidechain_from(s.tracks["KICK"])
```

## Render to wav

```python
print(s.render("out.wav"))
```

