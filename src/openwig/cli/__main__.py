"""`openwig` console-script entry point.

Subcommands:
    install        copy the bundled controller into Bitwig's user-scripts dir
    uninstall      remove the previously-installed controller
    doctor         check Python deps + controller install + bridge connection +
                   Bitwig version compatibility
    version        print the SDK version and supported Bitwig versions
"""
from __future__ import annotations

import argparse
import sys

from openwig import __version__, SUPPORTED_BITWIG_VERSIONS
from openwig.cli import install as _install


def _cmd_version(_args) -> int:
    print(f"openwig {__version__}")
    print(f"supports Bitwig Studio: {', '.join(sorted(SUPPORTED_BITWIG_VERSIONS))}")
    return 0


def _cmd_install(args) -> int:
    return _install.install_controller(force=args.force, dry_run=args.dry_run)


def _cmd_uninstall(args) -> int:
    return _install.uninstall_controller(dry_run=args.dry_run)


def _cmd_doctor(_args) -> int:
    return _install.doctor()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="openwig",
        description="Algorithmic composition for Bitwig Studio - install + diagnostics.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_inst = sub.add_parser("install", help="copy the controller script into Bitwig's user-scripts dir")
    p_inst.add_argument("--force", action="store_true", help="overwrite an existing controller file")
    p_inst.add_argument("--dry-run", action="store_true", help="report what would change without writing")
    p_inst.set_defaults(func=_cmd_install)

    p_un = sub.add_parser("uninstall", help="remove the controller from Bitwig's user-scripts dir")
    p_un.add_argument("--dry-run", action="store_true")
    p_un.set_defaults(func=_cmd_uninstall)

    p_doc = sub.add_parser("doctor", help="diagnose install + bridge + Bitwig version")
    p_doc.set_defaults(func=_cmd_doctor)

    p_ver = sub.add_parser("version", help="print SDK version + supported Bitwig versions")
    p_ver.set_defaults(func=_cmd_version)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
