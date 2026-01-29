"""Command line entrypoint for launching the Agent Sessions GUI."""

from __future__ import annotations

import argparse
import sys

from .server import serve


def main(argv: list[str] | None = None) -> None:
    """Start the HTTP server that powers the GUI."""
    parser = argparse.ArgumentParser(description="Start the Agent Sessions GUI server.")
    parser.add_argument("--host", default="127.0.0.1", help="Hostname to bind the HTTP server")
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port for the HTTP server (default: 8765)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable in-memory caching (reload sessions on every access)",
    )

    args = parser.parse_args(argv)

    refresh_interval = 0.0 if args.no_cache else None
    serve(host=args.host, port=args.port, refresh_interval=refresh_interval)


if __name__ == "__main__":
    main(sys.argv[1:])
