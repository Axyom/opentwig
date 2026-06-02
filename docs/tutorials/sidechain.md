# Sidechain pump

Two ways to make a bass duck on every kick. The SDK supports both: a real
sidechained `Compressor+` (audio-rate sidechain wiring), and an offline
"pump" automation on the bass volume (no routing needed; works even without
a real sidechain-capable device).

## Real sidechain - cross-track Compressor+

```python
from openwig import Song

s = Song(tempo=128, bars=4, clean=True)

kick = s.track("KICK", device="v9 Kick")
kick.clip([(36, b, 0.25, 1.0) for b in range(int(s.total))])
kick.fader(0.85)

bass = s.track("BASS", device="FM-4")
bass.fx("Compressor+")
# Tune the compressor for obvious ducking
bass._set_remote("Threshold", 0.20)
bass._set_remote("Ratio",     0.80)
bass._set_remote("Attack",    0.05)
bass._set_remote("Release",   0.30)
bass.clip([(33, 0, float(s.total), 0.85)])    # one held bass note

# Wire BASS's Compressor+ sidechain input from KICK's audio signal.
# sink_device_index = position of the Compressor+ in BASS's device chain.
bass.sidechain_from(kick, sink_device_index=1)
print("sidechain wired: BASS Compressor+ <- KICK signal")

s.play(loop=True)
```

What `sidechain_from(kick, sink_device_index=1)` does: it walks BASS's device
chain to the device at index 1 (the Compressor+ we just inserted), finds the
sidechain-input pin on that device, and connects it to KICK's track output-
the exact path Bitwig takes when you drag a sidechain wire in the GUI. There
is no public API for this; the SDK reaches into Bitwig's internal module
graph via reflection.

`sink_device_index=0` would be the FM-4 itself. We want the Compressor+ at
position 1.

## Offline pump - no real sidechain, no routing

If you want a "pumped" feel without wiring sidechain audio (no extra
Compressor+ needed, and it works on devices that don't accept sidechain input),
use `pump`:

```python
bass.pump(hi=0.82)
```

That's one line. The SDK writes an offline volume-automation curve on the bass
track that drops to `hi=0.82` of unity on every kick hit, then ramps back. No
real audio sidechain involved - it's a fast, deterministic ducking effect
written straight onto the arranger.

Parameters worth knowing:

- `hi=0.82` - volume after recovery (default 0.82 of unity).
- `duck=0.30` - volume at the dip (default 0.30 of unity; lower = deeper pump).
- `active=None` - by default pumps the whole song. Pass a list of `(start, end)`
  beat ranges to restrict to specific sections.

## When to use which

| Goal | Use |
|---|---|
| Realistic sidechain compression (transient + ratio + release behaviour) | `sidechain_from` |
| Fast, deterministic pump on any device (no routing, no extra plugin) | `pump` |
| Sidechain a synth modulator (LFO duck) instead of audio | `add_modulator` + `sidechain_from` on the modulator |

## Verifying it audibly

Render and check that the bass RMS dips on the kick hits. The package
ships `examples/verify_sidechain.py` - render, slide a 50 ms window through
the wav, compare RMS at kick beats vs between them:

```bash
python -m openwig.examples.verify_sidechain   # if you've cloned the source
```

Or write your own - see [Render to wav](render.md) for the loopback-capture
recipe.

## Next

- [Render to wav](render.md)
- [Algorithmic composition](algorithmic.md)
- [API: `Track.sidechain_from` / `Track.pump`](../reference.md)
