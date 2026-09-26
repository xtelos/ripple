"""The MCP server: five tools over one repository, spoken over stdio.

Each tool is a thin wrapper around a function in queries.py or checker.py.
The graph is rebuilt only when a file's mtime or size changes (see
cache.py), so an agent can edit and ask again without reindexing by hand.

readOnlyHint tells the client a tool changes nothing, which lets editors run
it without asking the user. It is set on the four graph queries and not on
check, because running a test suite can do anything the tests do.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, queries
from .cache import RepoIndex
from .checker import run_check

INSTRUCTIONS = """\
ripple answers questions about the call graph of this Python repository.
Before changing a function, call impact on it to see what calls it (grouped
by file) and which tests reach it; after the change, call check with those
tests. Symbols can be a full dotted path (pkg.module.Class.method), a suffix
(Class.method) or a bare name; an ambiguous name returns candidates.
The graph is static: calls through untyped values, getattr, dict dispatch
and decorator wrappers are not followed. impact lists same-named call sites
it could not resolve under possible_missed_callers; check those by hand.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
RUNS_CODE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def build_server(repo: str, test_command: str | None = None, timeout: float = 300) -> MCPServer:
    index = RepoIndex(repo)
    server = MCPServer("ripple", version=__version__, instructions=INSTRUCTIONS)

    @server.tool(
        annotations=READ_ONLY,
        description=(
            "List the functions and methods that directly call a symbol, with the file:line of each "
            "call site. symbol: dotted path, Class.method suffix, or bare name."
        ),
    )
    def callers(symbol: str) -> dict:
        return queries.callers(index.graph(), symbol)

    @server.tool(
        annotations=READ_ONLY,
        description=(
            "List what a function or method calls: resolved callees with call sites, plus the calls "
            "ripple could not resolve statically and why."
        ),
    )
    def callees(symbol: str) -> dict:
        return queries.callees(index.graph(), symbol)

    @server.tool(
        annotations=READ_ONLY,
        description=(
            "Answer 'what breaks if I change this?': every transitive caller of a symbol up to depth "
            "hops, grouped by file, with the tests that reach it listed separately so you know what to "
            "run. Also lists unresolved call sites with the same name, which static analysis cannot rule out."
        ),
    )
    def impact(symbol: str, depth: int = 5) -> dict:
        return queries.impact(index.graph(), symbol, depth=max(1, min(depth, 20)))

    @server.tool(
        annotations=READ_ONLY,
        description=(
            "Find the shortest call chain from one symbol to another, e.g. from a test or entry point "
            "to the function you are changing. Returns each hop with the file:line it is called from."
        ),
    )
    def path(source: str, target: str) -> dict:
        return queries.path(index.graph(), source, target)

    @server.tool(
        annotations=RUNS_CODE,
        description=(
            "Run the repository's test command (default: python -m pytest -q) and return pass or fail, "
            "the summary line and the first failing tests, never the full log. tests: optional test ids "
            "or paths to run instead of the whole suite, e.g. the ones impact listed."
        ),
    )
    def check(tests: list[str] | None = None) -> dict:
        return run_check(index.repo, command=test_command, timeout=timeout, extra_args=tests)

    return server
