"""Command line for the TypeScript tracer: install, status, trace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bench.ts_tracer import RUNNERS, install, trace, typecheck, unavailable


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.ts_tracer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="npm ci --ignore-scripts into the cache")
    sub.add_parser("status", help="say whether node and the cache are usable")
    sub.add_parser("typecheck", help="tsc --noEmit over the fixture")
    t = sub.add_parser("trace", help="run the fixture's tests under the tracer")
    t.add_argument("--runner", choices=RUNNERS, default="vitest")
    t.add_argument("--out", type=Path, help="write the edges as JSON here (default: stdout)")
    args = parser.parse_args(argv)

    if args.command == "install":
        install()
        return 0
    if args.command == "status":
        reason = unavailable()
        print(reason or "ready")
        return 1 if reason else 0
    if args.command == "typecheck":
        result = typecheck()
        print(result.stdout + result.stderr, end="")
        return result.returncode
    text = json.dumps(trace(args.runner), indent=1) + "\n"
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
