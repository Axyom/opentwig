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

    shutil.copyfile(src, dst)
    print(f"[openwig] installed -> {dst}")
    print("[openwig] next: Bitwig Studio -> Settings -> Controllers -> openwig -> Add -> OpenwigBridge")
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


def doctor() -> int:
    """Print install + bridge + Bitwig-version diagnostics. Exit non-zero on any failure."""
    from openwig import SUPPORTED_BITWIG_VERSIONS, __version__

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
        if b.wait_connected(2.0):
            try:
                snap = b.request("state.snapshot")
                ver = snap.get("bitwig_version") or snap.get("host_version") or "<unknown>"
                ok = ver.split(".")[0] in SUPPORTED_BITWIG_VERSIONS
                print(f"bridge :7777   : OK (Bitwig {ver}) {'compatible' if ok else 'INCOMPATIBLE'}")
                if not ok:
                    rc = max(rc, 3)
            except Exception as exc:  # noqa: BLE001
                print(f"bridge :7777   : OK (connected) but state.snapshot failed: {exc}")
                rc = max(rc, 3)
        else:
            print("bridge :7777   : NOT REACHABLE (start Bitwig, then re-run)")
            rc = max(rc, 2)
    except Exception as exc:  # noqa: BLE001
        print(f"bridge :7777   : error ({exc})")
        rc = max(rc, 2)

    return rc
