"""Command-line entry point: the same queries as the MCP tools, for humans.

    ripple index <repo>
    ripple callers|callees <symbol> [--repo DIR] [--json]
    ripple impact <symbol> [--depth N] [--limit N] [--repo DIR] [--json]
    ripple path <source> <target> [--repo DIR] [--json]
    ripple check [TEST ...] [--repo DIR] [--test-command CMD]
    ripple serve [--repo DIR] [--test-command CMD]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import queries
from .cache import load_graph
from .checker import run_check

MORE = "  ... and {n} more {what} not listed; raise --limit to see them"


def _print_lookup_error(result: dict) -> None:
    print(result["error"])
    for name in result.get("candidates", []) + result.get("suggestions", []):
        print(f"  {name}")


def format_index(repo: str, graph, cached: bool) -> str:
    stats = graph.stats()
    kinds = ", ".join(f"{n} {k}" for k, n in stats["symbols"].items())
    lines = [
        f"{repo}: {stats['files']} files, {kinds}",
        f"{stats['edges']} resolved calls, {stats['unresolved_calls']} unresolved calls, "
        f"{stats['external_calls']} calls to code outside the repo",
        "(loaded from cache)" if cached else "(indexed)",
    ]
    if graph.parse_errors:
        lines.append("could not parse: " + ", ".join(graph.parse_errors))
    if graph.skipped_dirs:
        shown = ", ".join(graph.skipped_dirs[:10])
        more = f" and {len(graph.skipped_dirs) - 10} more" if len(graph.skipped_dirs) > 10 else ""
        lines.append(f"skipped directories (not indexed): {shown}{more}")
    collisions = graph.module_collisions()
    if collisions:
        lines.append("warning: files share a module name, so their symbols share ids:")
        for module, files in collisions.items():
            lines.append(f"  {module}: {', '.join(files)}")
    return "\n".join(lines)


def format_symbols(title: str, entries: list[dict]) -> list[str]:
    lines = [f"{title} ({len(entries)}):"]
    for e in entries:
        lines.append(f"  {e['symbol']}  {e['location']}  called at {', '.join(e['call_sites'])}")
    return lines


def format_impact(result: dict) -> str:
    lines = [f"impact of {result['symbol']} (depth {result['depth']}): {result['affected']} callers"]
    for file, entries in result["by_file"].items():
        lines.append(f"  {file}")
        for e in entries:
            lines.append(f"    {e['symbol']}  line {e['line']}  ({e['distance']} hop{'s' * (e['distance'] > 1)})")
    if result["omitted_callers"]:
        lines.append(MORE.format(n=result["omitted_callers"], what="callers"))
    lines.append(f"tests that reach it ({result['test_count']}):")
    for t in result["tests"]:
        lines.append(f"  {t['node_id']}")
    if result["omitted_tests"]:
        lines.append(MORE.format(n=result["omitted_tests"], what="tests"))
    if result["more_beyond_depth"]:
        lines.append("more callers exist beyond this depth; raise --depth to see them")
    if result["possible_missed_callers"]:
        lines.append(f"unresolved call sites with the same name ({result['possible_missed_total']}), check by hand:")
        for m in result["possible_missed_callers"]:
            lines.append(f"  {m['call']}  in {m['caller']}  {m['location']}")
    return "\n".join(lines)


def format_result(command: str, result: dict) -> str:
    if command == "impact":
        return format_impact(result)
    if command == "callers":
        return "\n".join(format_symbols(f"callers of {result['symbol']}", result["callers"]))
    if command == "callees":
        lines = format_symbols(f"callees of {result['symbol']}", result["callees"])
        lines.append(f"unresolved ({len(result['unresolved'])}):")
        lines += [f"  {u['call']}  {u['location']}  {u['reason']}" for u in result["unresolved"]]
        return "\n".join(lines)
    if result["path"] is None:
        return f"no call chain from {result['from']} to {result['to']}"
    hops = [f"  {h['symbol']}  {h['location']}" + (f"  (called at {h['called_at']})" if h["called_at"] else "") for h in result["path"]]
    return "\n".join([f"call chain ({len(hops) - 1} hops):"] + hops)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ripple", description="Call graph and impact analysis for Python repos.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="index a repository and print what was found")
    p.add_argument("repo", nargs="?", default=".")

    for name, help_text in (
        ("callers", "who calls this symbol"),
        ("callees", "what this symbol calls"),
        ("impact", "everything that transitively calls this symbol, and the tests that reach it"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("symbol")
        p.add_argument("--repo", default=".")
        p.add_argument("--json", action="store_true", help="print the raw result as JSON")
        if name == "impact":
            p.add_argument("--depth", type=int, default=5)
            p.add_argument("--limit", type=int, default=queries.MAX_LISTED, help="most callers and tests to list")

    p = sub.add_parser("path", help="shortest call chain from one symbol to another")
    p.add_argument("source")
    p.add_argument("target")
    p.add_argument("--repo", default=".")
    p.add_argument("--json", action="store_true")

    for name, help_text in (("check", "run the test command once"), ("serve", "run the MCP server on stdio")):
        p = sub.add_parser(name, help=help_text)
        if name == "check":
            p.add_argument("tests", nargs="*", help="test paths or pytest node ids to run instead of the whole suite")
        p.add_argument("--repo", default=".")
        p.add_argument(
            "--test-command",
            default=os.environ.get("RIPPLE_TEST_COMMAND"),
            help="test command (default: python -m pytest -q, using the repo's .venv if present)",
        )
        p.add_argument("--timeout", type=float, default=300, help="seconds before the test run is killed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        from .server import build_server  # imported here so the CLI starts fast without the SDK

        build_server(args.repo, args.test_command, args.timeout).run("stdio")
        return 0

    if args.command == "check":
        try:
            result = run_check(args.repo, command=args.test_command, timeout=args.timeout, tests=args.tests)
        except ValueError as error:
            print(error)
            return 2
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1

    if args.command == "index":
        graph, cached = load_graph(args.repo)
        print(format_index(args.repo, graph, cached))
        return 0

    graph, _ = load_graph(args.repo)
    if args.command == "path":
        result = queries.path(graph, args.source, args.target)
    elif args.command == "impact":
        result = queries.impact(graph, args.symbol, depth=args.depth, limit=args.limit)
    else:
        result = getattr(queries, args.command)(graph, args.symbol)

    if "error" in result:
        _print_lookup_error(result)
        return 1
    print(json.dumps(result, indent=2) if args.json else format_result(args.command, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
