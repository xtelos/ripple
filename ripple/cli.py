"""Command-line entry point: the same queries as the MCP tools, for humans.

    ripple index <repo> [--reasons] [--lang auto|python|typescript]
    ripple callers|callees <symbol> [--repo DIR] [--json] [--kinds K] [--lang L]
    ripple impact <symbol> [--depth N] [--limit N] [--repo DIR] [--json] [--kinds K] [--lang L]
    ripple path <source> <target> [--repo DIR] [--json] [--kinds K] [--lang L]
    ripple check [TEST ...] [--repo DIR] [--test-command CMD] [--lang L]
    ripple serve [--repo DIR] [--test-command CMD] [--lang L]

--kinds (default all) picks the edges a question follows: calls, renders and renderer, comma separated. renders is
a function that writes <Child /> (TypeScript JSX), renderer the component React runs Child inside, proven or left out.
--lang (default auto, or RIPPLE_LANG) picks the language the repo is read as; auto looks at its files and
reads a repo that has both as both. Exit status: 0 done, 1 not found or the tests failed, 2 refused
(no such repo, nothing to read, a missing parser, a command that is not allowed), 3 answered, but part of
the repo was left out (a language whose parser is not installed or cannot load, or ids both languages
define): the output says which.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from . import queries
from .cache import load_repo
from .checker import run_check
from .languages import CHOICES, SHOWN_UNPARSABLE, RepoError, RepoGraph, check_profile, module_collisions, plural, with_warnings

MORE = "  ... and {n} more {what} not listed; raise --limit to see them"
PARTIAL_EXIT = 3


def _print_lookup_error(result: dict) -> None:
    print(result["error"])
    for name in result.get("candidates", []) + result.get("suggestions", []):
        print(f"  {name}")


def format_reasons(graph) -> list[str]:
    """Unresolved calls and calls outside the repo, each counted by cause (most common first)."""
    lines = ["unresolved calls by reason:"]
    unresolved = Counter(u.reason for u in graph.unresolved)
    lines += [f"  {n:6d}  {reason}" for reason, n in unresolved.most_common()]
    lines.append("calls outside the repo by reason:")
    lines += [f"  {n:6d}  {reason}" for reason, n in sorted(graph.external_reasons.items(), key=lambda kv: -kv[1])]
    return lines


def format_languages(result: RepoGraph) -> list[str]:
    """Each language's files read (the unparsable ones among them, counted the same way for both languages),
    when the repo has more than Python (a Python repo's report is as it was)."""
    lines = ["languages:"]
    for language in result.languages:
        if language.skipped:
            lines.append(f"  {language.label}: {plural(language.found, 'file')} found, not indexed")
            continue
        line = f"  {language.label}: {plural(language.files, 'file')}"
        if language.parse_errors:
            line += f" ({len(language.parse_errors)} could not be parsed)"
        lines.append(line)
    return lines


def format_unparsable(result: RepoGraph, named: bool) -> list[str]:
    """Each language's unparsable files, one per line, the first SHOWN_UNPARSABLE of them."""
    lines = []
    for language in result.languages:
        errors = language.parse_errors
        if not errors:
            continue
        where = f"{language.label}, " if named else ""
        lines.append(f"could not parse ({where}{plural(len(errors), 'file')}):")
        lines += [f"  {file}" for file in errors[:SHOWN_UNPARSABLE]]
        if len(errors) > SHOWN_UNPARSABLE:
            lines.append(f"  ... and {len(errors) - SHOWN_UNPARSABLE} more")
    return lines


def format_index(repo: str, result: RepoGraph, reasons: bool = False) -> str:
    graph = result.graph
    stats = graph.stats()
    kinds = ", ".join(f"{n} {k}" for k, n in stats["symbols"].items())
    jsx = sum(1 for e in graph.edges if e.kind in queries.JSX_KINDS)  # TypeScript JSX edges: not calls
    lines = [
        f"{repo}: {stats['files']} files, {kinds}",
        f"{stats['edges'] - jsx} resolved calls, " + (f"{jsx} JSX edges, " if jsx else "")
        + f"{stats['unresolved_calls']} unresolved calls, {stats['external_calls']} calls to code outside the repo",
        "(loaded from cache)" if result.cached else "(indexed)",
    ]
    named = any(language.name != "python" for language in result.languages)
    if named:
        lines += format_languages(result)
    lines += format_unparsable(result, named)
    if graph.skipped_dirs:
        shown = ", ".join(graph.skipped_dirs[:10])
        more = f" and {len(graph.skipped_dirs) - 10} more" if len(graph.skipped_dirs) > 10 else ""
        lines.append(f"skipped directories (not indexed): {shown}{more}")
    collisions = module_collisions(graph)
    if collisions:
        lines.append("warning: files share a module name, so their symbols share ids:")
        for module, files in collisions.items():
            lines.append(f"  {module}: {', '.join(files)}")
    lines += [f"warning: {warning}" for warning in result.warnings]
    if reasons:
        lines += format_reasons(graph)
    return "\n".join(lines)


KIND_WORDS = {"calls": "called", "renders": "renders", "renderer": "renderer"}


def format_sites(entry: dict) -> str:
    """Where an edge is written. An entry with a JSX edge says which kind each site is; any other is as it always was."""
    if "sites_by_kind" not in entry:
        return f"called at {', '.join(entry['call_sites'])}"
    return "; ".join(f"{KIND_WORDS[kind]} at {', '.join(sites)}" for kind, sites in entry["sites_by_kind"].items())


def format_symbols(title: str, entries: list[dict]) -> list[str]:
    lines = [f"{title} ({len(entries)}):"]
    for e in entries:
        lines.append(f"  {e['symbol']}  {e['location']}  {format_sites(e)}")
    return lines


def format_decorations(result: dict) -> list[str]:
    """Where the symbol is written as a decorator, when it is. These are not calls and are never listed as callers."""
    lines = []
    if result.get("decorations"):
        lines.append(f"applied as a decorator ({len(result['decorations'])}), not calls; what a decorator does is not modelled:")
        lines += [f"  {d['in']}  {d['applied_at']}  written {d['written']}" for d in result["decorations"]]
    if result.get("possible_decorations"):
        lines.append(
            f"applied as a decorator under a name ripple could not resolve ({len(result['possible_decorations'])}), check by hand:"
        )
        lines += [f"  {d['in']}  {d['applied_at']}  written {d['written']}" for d in result["possible_decorations"]]
    if result.get("decorations_omitted"):
        lines.append(MORE.format(n=result["decorations_omitted"], what="decorator sites"))
    if result.get("decorations_of_callers"):
        lines.append(
            f"callers that are applied as decorators ({len(result['decorations_of_callers'])}), not calls; the reach continues "
            "past these sites and what a decorator does is not modelled:"
        )
        lines += [
            f"  {d['in']}  {d['applied_at']}  written {d['written']}  (the caller {d['decorator']})"
            for d in result["decorations_of_callers"]
        ]
    if result.get("decorations_of_callers_omitted"):
        lines.append(MORE.format(n=result["decorations_of_callers_omitted"], what="decorator sites of callers"))
    return lines


def format_missed(m: dict) -> str:
    inside = "  (inside a class ripple does not read)" if "reason" in m else ""
    return f"  {m['call']}  in {m['caller']}  {m['location']}{inside}"


def format_impact(result: dict) -> str:
    lines = [f"impact of {result['symbol']} (depth {result['depth']}): {result['affected']} callers"]
    for file, entries in result["by_file"].items():
        lines.append(f"  {file}")
        for e in entries:
            through = f"  through the wrapper {e['via_wrapper']}" if "via_wrapper" in e else ""
            through += f"  via {', '.join(e['edge_kinds'])}" if "edge_kinds" in e else ""
            lines.append(f"    {e['symbol']}  line {e['line']}  ({e['distance']} hop{'s' * (e['distance'] > 1)}){through}")
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
            lines.append(format_missed(m))
    lines += format_decorations(result)
    return "\n".join(lines)


def format_result(command: str, result: dict) -> str:
    """The text of an answer; one that resolved its name through a module's export says how, first."""
    text = format_answer(command, result)
    return f"note: {result['note']}\n{text}" if "note" in result else text


def format_answer(command: str, result: dict) -> str:
    if command == "impact":
        return format_impact(result)
    if command == "callers":
        lines = format_symbols(f"callers of {result['symbol']}", result["callers"])
        if result.get("possible_missed_callers"):
            lines.append(f"calls with the same name inside classes ripple does not read ({result['possible_missed_total']}), check by hand:")
            lines += [format_missed(m) for m in result["possible_missed_callers"]]
        if result["through_wrapper"]:
            lines.append(f"lines that call it by name, through its wrapper ({len(result['through_wrapper'])}):")
            lines += [
                f"  {e['symbol']}  {e['location']}  called at {', '.join(e['call_sites'])}  via {e['via_wrapper']}"
                for e in result["through_wrapper"]
            ]
        return "\n".join(lines + format_decorations(result))
    if command == "callees":
        lines = format_symbols(f"callees of {result['symbol']}", result["callees"])
        lines.append(f"unresolved ({len(result['unresolved'])}):")
        lines += [f"  {u['call']}  {u['location']}  {u['reason']}" for u in result["unresolved"]]
        if result.get("applies_decorators"):
            lines.append(f"applies decorators ({len(result['applies_decorators'])}), not calls; what a decorator does is not modelled:")
            lines += [
                f"  {d['decorator'] or 'a name ripple could not resolve'}  {d['applied_at']}  written {d['written']}"
                for d in result["applies_decorators"]
            ]
        return "\n".join(lines)
    if result["path"] is None:
        return "\n".join([f"no call chain from {result['from']} to {result['to']}"] + format_decorations(result))
    hops = [
        f"  {h['symbol']}  {h['location']}"
        + (f"  ({'/'.join(h['edge_kinds']) if 'edge_kinds' in h else 'called'} at {h['called_at']})" if h["called_at"] else "")
        for h in result["path"]
    ]
    return "\n".join([f"call chain ({len(hops) - 1} hops):"] + hops + format_decorations(result))


def kinds_argument(value: str):
    try:
        return queries.parse_kinds(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def add_kinds(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--kinds",
        type=kinds_argument,
        default=None,
        metavar="KINDS",
        help="the edges to follow, comma separated: calls, renders, renderer, or all (default). renders: a function that "
        "writes <Child /> (changing Child can break it); renderer: the component React runs Child inside, when proven",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ripple", description="Call graph and impact analysis for Python and TypeScript repos.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="index a repository and print what was found")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--reasons", action="store_true", help="also count unresolved calls and calls outside the repo by cause")

    for name, help_text in (
        ("callers", "who calls this symbol"),
        ("callees", "what this symbol calls"),
        ("impact", "everything that transitively calls this symbol, and the tests that reach it"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("symbol")
        p.add_argument("--repo", default=".")
        p.add_argument("--json", action="store_true", help="print the raw result as JSON")
        add_kinds(p)
        if name == "impact":
            p.add_argument("--depth", type=int, default=5)
            p.add_argument("--limit", type=int, default=queries.MAX_LISTED, help="most callers and tests to list")

    p = sub.add_parser("path", help="shortest call chain from one symbol to another")
    p.add_argument("source")
    p.add_argument("target")
    p.add_argument("--repo", default=".")
    p.add_argument("--json", action="store_true")
    add_kinds(p)

    for name, help_text in (("check", "run the test command once"), ("serve", "run the MCP server on stdio")):
        p = sub.add_parser(name, help=help_text)
        if name == "check":
            p.add_argument("tests", nargs="*", help="test paths or pytest node ids to run instead of the whole suite")
        p.add_argument("--repo", default=".")
        p.add_argument(
            "--test-command",
            default=os.environ.get("RIPPLE_TEST_COMMAND"),
            help="test command (default: python -m pytest -q, using the repo's .venv or venv if present; a TypeScript-only repo, or --lang typescript, has no default)",
        )
        p.add_argument("--timeout", type=float, default=300, help="seconds before the test run is killed")

    for p in sub.choices.values():
        p.add_argument(
            "--lang",
            choices=CHOICES,
            default=os.environ.get("RIPPLE_LANG", "auto"),
            help="language to read the repo as (default: auto, from the files it has; env RIPPLE_LANG)",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except RepoError as error:
        # stderr: for `serve` stdout is the protocol channel, and an error is not an answer to a query
        print(f"ripple: {error}", file=sys.stderr)
        return 2


def run(args: argparse.Namespace) -> int:
    if args.command == "serve":
        from .server import build_server  # imported here so the CLI starts fast without the SDK

        build_server(args.repo, args.test_command, args.timeout, args.lang).run("stdio")
        return 0

    if args.command == "check":
        profile = check_profile(args.repo, args.lang)
        try:
            result = run_check(args.repo, command=args.test_command, timeout=args.timeout, tests=args.tests, profile=profile)
        except ValueError as error:
            print(error)
            return 2
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1

    loaded = load_repo(args.repo, lang=args.lang)
    exit_code = PARTIAL_EXIT if loaded.incomplete else 0
    if args.command == "index":
        print(format_index(args.repo, loaded, args.reasons))
        return exit_code

    graph = loaded.graph
    if args.command == "path":
        result = queries.path(graph, args.source, args.target, kinds=args.kinds)
    elif args.command == "impact":
        result = queries.impact(graph, args.symbol, depth=args.depth, limit=args.limit, kinds=args.kinds)
    else:
        result = getattr(queries, args.command)(graph, args.symbol, kinds=args.kinds)

    # What the answer does not cover goes with it: in the JSON, and on stderr beside the text.
    if "error" in result or not args.json:
        for warning in loaded.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    if "error" in result:
        _print_lookup_error(result)
        return 1
    print(json.dumps(with_warnings(result, loaded.warnings), indent=2) if args.json else format_result(args.command, result))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
