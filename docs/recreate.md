# Recreate (Bitwig -> script)

The reverse direction: compose or edit a track **in Bitwig**, then read the open
project back into a Python script that rebuilds it. openwig walks Bitwig's own
document objects live, so it captures real values - no project-file parsing.

```bash
python -m openwig recreate -o my_song.py
```

That reads the open project (it **stops playback first** - walking the graph
during playback can crash) and writes a self-contained `my_song.py`. Run it like
any other openwig script to reconstruct the project.

## What it captures

- **Tempo**, track names, mix (fader, pan, mute/solo/arm)
- **Device chains** by name + each device's active remote-control values
- **MIDI clips** - start, duration, and every note (key, start, duration,
  velocity, channel)
- **Arranger automation** - volume/pan lanes, and device-parameter lanes with their
  **target resolved** (it figures out which device + `remote_index` each lane drives,
  via object-id matching) and emits `select_device(i)` + `automate('remote', ..., remote_index=N)`.
  Note: breakpoint **values are read in each parameter's native units** (e.g. a filter
  cutoff in Hz), so the target and curve shape are faithful but the absolute values of
  non-normalized params may need scaling
- **Effect/return tracks**

## What it does not capture (read-API gaps)

- Track input/output routing and send levels (not in the snapshot)
- Sidechain wiring
- Per-clip automation lanes (only arranger-level lanes are read)
- Plugin (VST) internal state
- The master device chain - fill in `s.master([...])` yourself

The generated script's header lists exactly what was and wasn't recreated for
your specific project, and leaves TODO comments where something needs your input.

## From Python

```python
from openwig.bridge import BridgeClient
from openwig.read import read_project, summarize
from openwig.recreate import to_script

b = BridgeClient(); b.start(); b.wait_connected(8)
data = read_project(b, with_clips=True)   # structure + notes + automation
print(summarize(data))
open("my_song.py", "w").write(to_script(data, project_label="my_song"))
```

`read_project(b, with_clips=False)` is much faster - structure only, no note walk.

## The workflow

1. Sketch a track naturally in Bitwig (draw clips, tweak devices, draw automation).
2. `python -m openwig recreate -o song.py`.
3. Refine `song.py` in code - that's where generators, variation, and
   parameterization live.
4. Run it to rebuild, listen, repeat.
