"""The MCP server: five tools over one repository, spoken over stdio.

Each tool is a thin wrapper around a function in queries.py or checker.py.
The graph is rebuilt only when a file's mtime or size changes (see
cache.py), so an agent can edit and ask again without reindexing by hand.

readOnlyHint tells the client a tool changes nothing, which lets editors run
it without asking the user. It is set on the four graph queries. check is
marked destructive instead: it only accepts test paths and node ids inside
the repo, but running a test suite can still do anything the tests do.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, queries
from .cache import RepoIndex
from .checker import run_check
from .languages import RepoError, check_profile, check_repo, selected_profiles, with_warnings

INSTRUCTIONS = """\
ripple answers questions about the call graph of this repository, written in
Python, TypeScript or both. Before changing a function, call impact on it to
see what calls it (grouped by file) and which tests reach it; after the
change, call check with the node_id of each of those tests (TypeScript tests
are not collected yet: every caller of a TypeScript symbol is listed as code).
A Python symbol is a full dotted path (pkg.module.Class.method), a suffix
(Class.method) or a bare name. A TypeScript symbol is its file path without
the extension, a colon and the member (src/util/math:clamp, src/cart/cart:Cart.add),
and Class.method, the path tail (math:clamp) or a bare member name work too. An
ambiguous name returns candidates; pick one, ripple never does.
The graph is static: calls through untyped values, getattr, dict dispatch,
computed keys, interface-typed values and decorators ripple cannot read are
not followed. A decorated Python function is called by its wrapper; callers
also lists, under through_wrapper, the lines that write its name. impact
lists same-named call sites it could not resolve under possible_missed_callers;
check those by hand. A TypeScript decorator is not a call edge: callers, impact and path list each place the
symbol is written as a decorator under decorations (and, where the name did not resolve, possible_decorations),
with a note that what a decorator does to what it decorates is not modelled. In a TypeScript repo, JSX makes two more kinds of edge: renders (a function that writes
<Child />, so changing Child can break it) and renderer (the component React runs Child inside, only when the
source proves it). Every answer follows all kinds by default; pass kinds (calls, renders, renderer, comma
separated) to follow some, and an entry that has a JSX edge says which kinds in edge_kinds. If a result has a warnings list, part of the repository
was left out (TypeScript without a parser that loads, or ids both languages
define): the answer covers only the rest, and the warning says what and why.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
RUNS_CODE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def build_server(repo: str, test_command: str | None = None, timeout: float = 300, lang: str = "auto") -> MCPServer:
    """The server for one repository. lang is "auto" (the languages its files are in), "python" or "typescript"."""
    check_repo(repo)  # a repo that is not there fails now, with a message, not at the first question
    selected_profiles(lang)
    index = RepoIndex(repo, lang=lang)
    server = MCPServer("ripple", version=__version__, instructions=INSTRUCTIONS)

    def parse_kinds(kinds: str):
        try:
            return queries.parse_kinds(kinds)
        except ValueError as error:
            raise ToolError(str(error)) from error

    def answer(query, *args, **kwargs) -> dict:
        """A query's result, with a `warnings` list when part of the repo was not indexed."""
        try:
            loaded = index.load()
        except RepoError as error:
            raise ToolError(str(error)) from error  # the agent sees this message; other errors are masked
        return with_warnings(query(loaded.graph, *args, **kwargs), loaded.warnings)

    @server.tool(
        annotations=READ_ONLY,
        description=(
            "List the functions and methods that directly call a symbol, with the file:line of each "
            "call site. A decorated function is called by its wrapper, so the lines that write its name "
            "are listed apart, under through_wrapper. Where a TypeScript symbol is written as a decorator, those places are listed under decorations (not calls; what a decorator does is not modelled). symbol: a Python dotted path, a TypeScript path:member id "
            "(src/util/math:clamp), a Class.method suffix, or a bare name. kinds: which edges to list, comma "
            "separated, from calls, renders (a function that writes <Symbol /> in JSX) and renderer (the component "
            "React runs the symbol inside, when proven); default all. An entry with a JSX edge has edge_kinds."
        ),
    )
    def callers(symbol: str, kinds: str = "all") -> dict:
        return answer(queries.callers, symbol, kinds=parse_kinds(kinds))

    @server.tool(
        annotations=READ_ONLY,
        description=(
            "List what a function or method calls: resolved callees with call sites, plus the calls "
            "ripple could not resolve statically and why. kinds is as for callers; a TypeScript component's "
            "children are renders edges, and the elements whose renderer is not proven are listed as unresolved."
        ),
    )
    def callees(symbol: str, kinds: str = "all") -> dict:
        return answer(queries.callees, symbol, kinds=parse_kinds(kinds))

    @server.tool(
        annotations=READ_ONLY,
        description=(
            "Answer 'what breaks if I change this?': every transitive caller of a symbol up to depth "
            "hops, grouped by file, with the Python tests that reach it listed separately (each with the pytest "
            "node_id to pass to check; TypeScript tests are not collected, so their callers are listed as code). "
            "At most limit callers and limit tests are listed; truncated and omitted_callers / omitted_tests say how many more were found (decorator sites are cut too: decorations_omitted, and truncated counts them). Also lists unresolved call "
            "sites with the same name, which static analysis cannot rule out, and where a TypeScript symbol is written as a decorator (decorations, not calls), including where a caller it found is itself a decorator (decorations_of_callers: the reach continues past those sites). Follows every edge kind by default: a "
            "TypeScript component that writes <Symbol /> is affected too (it can break when the symbol changes), and a "
            "caller reached through one carries edge_kinds; pass kinds (calls, renders, renderer) to follow fewer."
        ),
    )
    def impact(symbol: str, depth: int = 5, limit: int = queries.MAX_LISTED, kinds: str = "all") -> dict:
        return answer(queries.impact, symbol, depth=max(1, min(depth, 20)), limit=max(1, min(limit, 1000)), kinds=parse_kinds(kinds))

    @server.tool(
        annotations=READ_ONLY,
        description=(
            "Find the shortest call chain from one symbol to another, e.g. from a test or entry point "
            "to the function you are changing. Returns each hop with the file:line it is called from, and edge_kinds "
            "on a hop that is a JSX render edge. kinds (default all) limits the edges followed, as for callers."
        ),
    )
    def path(source: str, target: str, kinds: str = "all") -> dict:
        return answer(queries.path, source, target, kinds=parse_kinds(kinds))

    @server.tool(
        annotations=RUNS_CODE,
        description=(
            "Run the repository's test command and return pass or fail, the summary line and the first "
            "failing tests, never the full log. The command is the one the server was started with; with none, "
            "the Python profile (a repo with any Python file, or --lang python) runs python -m pytest -q (the "
            "repo's .venv or venv if it has one) and the TypeScript profile (a repo with only TypeScript files, "
            "or --lang typescript) has no default and refuses. tests: optional test file or directory paths "
            "inside the repo, or for Python pytest node ids (path::name, e.g. the node_id of each test impact "
            "listed), added to that command to run instead of the whole suite. A TypeScript test id (ripple's "
            "own, or a runner's line filter such as a file path with :12) is refused: pass the file path. "
            "Options (anything starting with '-') are refused."
        ),
    )
    def check(tests: list[str] | None = None) -> dict:
        try:
            profile = check_profile(index.repo, lang)
            return run_check(index.repo, command=test_command, timeout=timeout, tests=tests, profile=profile)
        except ValueError as error:  # includes RepoError, and a language with no default test command
            raise ToolError(str(error)) from error  # the agent sees this message; other errors are masked

    return server
