"""Command-line entry point.

``cerepulse`` with no subcommand launches the desktop app; subcommands cover development
and diagnostic tasks. Subcommand modules are imported lazily so that, for example, running
``capture`` never pulls in Qt.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from cerepulse import __about__ as about
from cerepulse.core import paths
from cerepulse.core.config import load_config
from cerepulse.core.logging_setup import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cerepulse",
        description=f"{about.NAME} — {about.TAGLINE}",
    )
    parser.add_argument("--version", action="version", version=f"{about.NAME} {about.VERSION}")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override the configured log level.",
    )

    sub = parser.add_subparsers(dest="command")

    capture = sub.add_parser(
        "capture",
        help="Sign in to the portal and dump raw HTML fixtures (development tool).",
    )
    capture.add_argument(
        "--out",
        default="Research/captures",
        help="Directory to write captured pages into (default: Research/captures).",
    )
    capture.add_argument(
        "--secrets",
        default=".secrets.toml",
        help="TOML file holding portal credentials (default: .secrets.toml).",
    )

    sub.add_parser("paths", help="Print resolved data directories and exit.")

    sync = sub.add_parser(
        "sync",
        help="Sign in and refresh the local cache without opening a window.",
    )
    sync.add_argument("--year", type=int, default=None, help="Year to sync (default: today).")
    sync.add_argument("--month", type=int, default=None, help="Month to sync (default: today).")
    sync.add_argument(
        "--secrets",
        default=".secrets.toml",
        help="TOML file holding portal credentials (default: .secrets.toml).",
    )
    sync.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip fetching per-day punch detail.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    paths.ensure_dirs()
    config = load_config()
    configure_logging(level=args.log_level or config.logging.level)

    if args.command == "capture":
        from cerepulse.capture import run_capture

        return run_capture(out_dir=args.out, secrets_path=args.secrets, config=config)

    if args.command == "sync":
        from cerepulse.headless import run_sync

        return run_sync(
            config=config,
            secrets_path=args.secrets,
            year=args.year,
            month=args.month,
            backfill=not args.no_backfill,
        )

    if args.command == "paths":
        print(f"data root:  {paths.data_root()}")
        print(f"config:     {paths.config_file()}")
        print(f"database:   {paths.database_file()}")
        print(f"logs:       {paths.logs_dir()}")
        print(f"portable:   {paths.is_portable()}")
        return 0

    from cerepulse.ui.app import run_app

    return run_app(config)


if __name__ == "__main__":
    sys.exit(main())
