"""Cross-platform controller installer.

Bitwig Studio reads user-controller scripts from a well-known directory per
platform. We bundle `openwig_bridge.control.js` as package data and copy it there
on `openwig install`.

Paths (per Bitwig docs):
    Windows : %USERPROFILE%\\Documents\\Bitwig Studio\\Controller Scripts
    macOS   : ~/Documents/Bitwig Studio/Controller Scripts
    Linux   : ~/Bitwig Studio/Controller Scripts
"""
from __future__ import annotations

import shutil
import sys
from importlib.resources import as_file, files
from pathlib import Path

CONTROLLER_FILENAME = "openwig_bridge.control.js"
DEFAULTS_FILENAME = "symbols_default.json"  # bootstrap obfuscated-symbol mapping (DATA)


def _data_dir() -> Path:
    """openwig data dir where the controller reads the defaults + writes the cache/log.
    Must match _resolveLogPath() in the controller."""
    import os
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "openwig"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "openwig"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "openwig"


def _bitwig_user_scripts_dir() -> Path:
    """Return the per-platform Bitwig user-controller-scripts directory."""
    home = Path.home()
    if sys.platform == "win32":
        return home / "Documents" / "Bitwig Studio" / "Controller Scripts"
    if sys.platform == "darwin":
        return home / "Documents" / "Bitwig Studio" / "Controller Scripts"
    return home / "Bitwig Studio" / "Controller Scripts"


def _bundled_controller() -> Path:
    """Locate the bundled controller .js inside the installed package."""
    res = files("openwig.controller").joinpath(CONTROLLER_FILENAME)
    with as_file(res) as p:
        return Path(p)


def install_controller(*, force: bool = False, dry_run: bool = False) -> int:
    src = _bundled_controller()
    dst_dir = _bitwig_user_scripts_dir()
    dst = dst_dir / CONTROLLER_FILENAME

    if not dst_dir.exists():
        print(f"[openwig] controller dir not found: {dst_dir}", file=sys.stderr)
        print("              -> launch Bitwig Studio at least once, then re-run.", file=sys.stderr)
        return 2

    if dst.exists() and not force:
        print(f"[openwig] {dst} already exists - pass --force to overwrite.")
        return 1

    if dry_run:
        verb = "overwrite" if dst.exists() else "install"
        print(f"[openwig] would {verb} -> {dst}")
        return 0

    existed = dst.exists()
    shutil.copyfile(src, dst)
    print(f"[openwig] installed -> {dst}")

    # Copy the bootstrap symbol-mapping DATA file to the openwig data dir, where the controller
    # reads it at init. The obfuscated names live here as data, not in the controller code.
    try:
        data_dst = _data_dir()
        data_dst.mkdir(parents=True, exist_ok=True)
        res = files("openwig.controller").joinpath(DEFAULTS_FILENAME)
        with as_file(res) as p:
            shutil.copyfile(Path(p), data_dst / DEFAULTS_FILENAME)
        print(f"[openwig] symbol defaults -> {data_dst / DEFAULTS_FILENAME}")
    except Exception as exc:  # noqa: BLE001
        print(f"[openwig] WARNING: could not install symbol defaults: {exc}", file=sys.stderr)

    if existed:
        # Bitwig watches this file and auto-reloads the script a few seconds after it
        # changes - new handlers go live with no manual step. (A reload can leave value
        # setters / observers flaky; if controls act stale, remove + re-add the
        # controller once in Settings -> Controllers to fully reset it.)
        print("[openwig] Bitwig will auto-reload the controller in a few seconds.")
    else:
        print("[openwig] next: Bitwig Studio -> Settings -> Controllers -> openwig -> Add -> OpenwigBridge")
    # doctor is mandatory: the bridge refuses all ops until the symbols are validated + cached
    # for this exact Bitwig build. This must be run once per build (re-run after a Bitwig update).
    print("[openwig] REQUIRED: run `openwig doctor` once to validate + cache symbols for this build.")
    return 0


def uninstall_controller(*, dry_run: bool = False) -> int:
    dst = _bitwig_user_scripts_dir() / CONTROLLER_FILENAME
    if not dst.exists():
        print(f"[openwig] nothing to remove (not installed at {dst}).")
        return 0
    if dry_run:
        print(f"[openwig] would remove -> {dst}")
        return 0
    dst.unlink()
    print(f"[openwig] removed -> {dst}")
    return 0


def _print_selftest(rep) -> int:
    """Print the resolver self-test capability matrix; return the rc contribution."""
    if not rep.get("connected"):
        print("internals      : (bridge dropped during self-test)")
        return 2

    classes = rep.get("classes") or {}
    nload = sum(1 for v in classes.values() if v)
    missing = [k for k, v in classes.items() if not v]
    line = f"  classes      : {nload}/{len(classes)} internal classes load"
    if missing:
        line += "   MISSING: " + ", ".join(missing)
    print(line)

    caps = rep.get("capabilities")
    if not caps:
        print(f"  round-trip   : NOT RUN ({rep.get('error', 'unknown')})")
        return 3

    rc = 0
    for key, label in (("automation_write", "automation  "),
                       ("clip_create", "clip create "),
                       ("descriptor_read", "descriptor  "),
                       ("serialize", "serialize   "),
                       ("normalize", "normalize   ")):
        c = caps.get(key) or {}
        ok = c.get("ok")
        print(f"  {label} : {'OK  ' if ok else 'FAIL'}  ({c.get('detail', '')})")
        if not ok:
            rc = max(rc, 3)

    audio = rep.get("audio")
    if audio is not None:
        ok = audio.get("ok")
        detail = audio.get("detail", "")
        if audio.get("hrv"):
            detail += f" (hrv={audio.get('hrv')})"
        print(f"  audio clip   : {'OK  ' if ok else 'FAIL'}  ({detail})")
        if not ok:
            rc = max(rc, 3)

    disc = rep.get("discovered")
    if disc:
        print(f"  automation   : structural discovery "
              f"(al={disc.get('al_accessor')}, insert={disc.get('insert')}, base={disc.get('value_base')})")

    rd = rep.get("reader")
    if rd:
        print(f"  reader       : resolved (mX_={rd.get('mX_')}, ngq={rd.get('ngq')}, uEK={rd.get('uEK')})")
    cmds = rep.get("commands")
    if cmds:
        cc, nc = cmds.get("clipCmd") or {}, cmds.get("noteCmd") or {}
        tag = "by op-id" if cmds.get("resolved") else "SEED (op-id lookup failed)"
        print(f"  commands     : {tag} (clip={cc.get('cls')}.{cc.get('factory')}, note={nc.get('cls')}.{nc.get('factory')})")
    cache = rep.get("cache") or {}
    if cache.get("written"):
        print(f"  cache        : written -> {cache.get('path')}")
    elif "reason" in cache:
        print(f"  cache        : not written ({cache.get('reason')})")
    if rep.get("symbol_source"):
        print(f"  symbol source: {rep.get('symbol_source')}")

    if rep.get("ok"):
        print("  => all reflection paths verified on this Bitwig build")
    else:
        print("  => SOME paths failed - this Bitwig build may be unsupported.")
        print("     Please report at https://github.com/Axyom/openwig/issues with the lines above.")
    return rc


def doctor() -> int:
    """Print install + bridge + Bitwig-version + reflection self-test diagnostics.
    Exit non-zero on any failure."""
    from openwig import SUPPORTED_BITWIG_VERSIONS, __version__
    from openwig.diagnostics import run_selftest

    supported_str = ", ".join(f"{v}.x" for v in sorted(SUPPORTED_BITWIG_VERSIONS))
    print(f"openwig {__version__} (supports Bitwig: {supported_str})")
    rc = 0

    dst = _bitwig_user_scripts_dir() / CONTROLLER_FILENAME
    print(f"controller dir : {dst.parent}")
    print(f"controller     : {'OK' if dst.exists() else 'MISSING'}  ({dst})")
    if not dst.exists():
        rc = max(rc, 2)

    # Try a live bridge connection (best-effort; non-fatal if Bitwig isn't running).
    try:
        from openwig.bridge import BridgeClient

        b = BridgeClient()
        b.start()
        if not b.wait_connected(2.0):
            print("bridge :7777   : NOT REACHABLE (start Bitwig, then re-run)")
            return max(rc, 2)

        try:
            ver = b.host_version() or "<unknown>"
        except Exception:  # noqa: BLE001
            ver = "<unknown>"
        ok = ver.split(".")[0] in SUPPORTED_BITWIG_VERSIONS
        print(f"bridge :7777   : OK (Bitwig {ver}) {'compatible' if ok else 'INCOMPATIBLE'}")
        if not ok:
            rc = max(rc, 3)

        # Reflection self-test: prove the internal-access paths actually work on THIS build.
        # Runs on a throwaway track that is created and deleted here; existing tracks are
        # never touched.
        print("internals      : self-test on a throwaway track ...")
        try:
            rc = max(rc, _print_selftest(run_selftest(b)))
        except Exception as exc:  # noqa: BLE001
            print(f"  self-test    : ERROR ({exc})")
            rc = max(rc, 3)
        b.stop()
    except Exception as exc:  # noqa: BLE001
        print(f"bridge :7777   : error ({exc})")
        rc = max(rc, 2)

    return rc
