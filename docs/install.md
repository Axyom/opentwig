# Install

## Requirements

- **Bitwig Studio 6**
- **Python 3.11+**
- **Windows**

## 1. Install the Python package

```bash
pip install openwig
```

## 2. Install the controller script

```bash
python -m openwig install
```

This copies the bundled `openwig_bridge.control.js` into Bitwig's controller
scripts directory (`%USERPROFILE%\Documents\Bitwig Studio\Controller Scripts\`).

!!! tip "If install says the directory doesn't exist"
    Launch Bitwig Studio once (so it creates its user directory), then re-run.

## 3. Enable the controller in Bitwig

1. Open Bitwig Studio.
2. **Settings -> Controllers -> openwig -> Add -> OpenwigBridge**.
3. One-time. Bitwig remembers it across launches.

## 4. Validate (required)

```bash
python -m openwig doctor
```

!!! warning "doctor is mandatory"
    openwig refuses every operation until `doctor` has validated and cached the
    obfuscated symbols for your exact Bitwig build. Run it once after installing,
    and again after any Bitwig update (the cache is keyed to the build). Until then
    the bridge replies to normal calls with "symbols are not validated for this
    Bitwig build. Run `openwig doctor`."

Expected output:

```
openwig 0.1.3 (supports Bitwig: 6.x)
controller dir : C:\Users\<you>\Documents\Bitwig Studio\Controller Scripts
controller     : OK
bridge :7777   : OK (Bitwig 6.0.6) compatible
internals      : self-test on a throwaway track ...
  classes      : 9/9 internal classes load
  automation   : OK
  clip create  : OK
  descriptor   : OK
  serialize    : OK
  normalize    : OK
  cache        : written -> ...\openwig\symbols_cache.json
  => all reflection paths verified on this Bitwig build
```

`doctor` runs a self-test on a temporary track (created and deleted automatically,
existing tracks untouched) that confirms openwig's reflection paths work on your exact
Bitwig build. If any line says `FAIL`, that build is unsupported: please
[open an issue](https://github.com/Axyom/openwig/issues) with the output.

It also resolves Bitwig's obfuscated internal names and writes a small
`symbols_cache.json` keyed to your exact build; openwig loads it on connect so it
keeps working across Bitwig updates that re-obfuscate those names. Re-run `doctor`
after updating Bitwig to refresh the cache.

Then write your [first song](quickstart.md).

## Running the tests

Unit tests need no Bitwig and run anywhere:

```bash
pip install -e .[dev]
pytest
```

The live auto-adaptability tests drive a real Bitwig (they create and delete a throwaway
probe track to verify the resolver against your actual build). They require Bitwig running
with the OpenwigBridge controller enabled and are opt-in:

```bash
OPENWIG_LIVE=1 pytest -m live
```

Without `OPENWIG_LIVE=1` the live tests are skipped, so a plain `pytest` (and CI) never
touches a running Bitwig.

## Uninstall

```bash
python -m openwig uninstall   # removes the controller .js (you keep the pip package)
pip uninstall openwig
```
