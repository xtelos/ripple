"""The questions an agent asks before editing: who calls this, what does it
call, what could break, and how does A reach B.

Every function takes a symbol as the user typed it (a full dotted path, a
Class.method suffix, or a bare name) and returns a plain dict ready to be
serialized. When the name is ambiguous or unknown, the dict says so and lists
candidates instead of picking one.
"""

from __future__ import annotations

import difflib
import re
from collections import deque

from .model import Edge, Graph, Symbol
from .profile import PYTHON, TYPESCRIPT, LanguageProfile
from .pytest_collection import PytestCollection  # noqa: F401 (it was defined here)
from .ts_modules import SUFFIXES, module_id  # no parser needed to read an id

MAX_LISTED = 100  # default cap on symbols listed per section, so a hub function cannot flood the agent


_CLASH = re.compile(r"#\d+$")


def _is_path_id(symbol_id: str) -> bool:
    """A TypeScript id: a path, and for a member a colon (src/util/math:clamp). A Python id has neither."""
    return ":" in symbol_id or "/" in symbol_id


def _stem(file: str) -> str:
    return next((file[: -len(suffix)] for suffix in SUFFIXES if file.endswith(suffix)), file)


def _module_ids(graph: Graph, path: str) -> list[str]:
    """The module ids a path in a query names, read through the ids the indexer gave the files.

    An id is not the path folded by a rule: `src/a.ts` beside `src/a/index.ts` leaves the index file
    the id `src/a/index`, and a root `util.ts` is `util`, never `src/util`. So the graph's own map of
    file to module id decides. A file's path (with or without its extension) or a module id that is
    exactly the query wins; a trailing slash is the index file of that directory; only when nothing
    matches exactly is the query taken for the tail of a path, and then every module it ends is
    returned, for the caller to call ambiguous.
    """
    known = graph.modules or {s.file: s.id for s in graph.symbols.values() if s.kind == "module"}
    files = {f: m for f, m in known.items() if f.endswith(SUFFIXES)}
    if path.endswith("/"):
        directory = path.rstrip("/")
        return sorted({m for f, m in files.items() if _stem(f) == (directory + "/index").lstrip("/")})
    exact = {m for f, m in files.items() if path in (f, _stem(f))} | ({path} & set(files.values()))
    if exact:
        return sorted(exact)
    tail = "/" + path
    return sorted({m for f, m in files.items() if f.endswith(tail) or _stem(f).endswith(tail) or m.endswith(tail)})


def _path_matches(graph: Graph, query: str) -> list[str]:
    """The ids a query shaped like a path names: its path part through _module_ids, then the member as
    written, with or without a clash suffix (`A.make` is `A.make` and `A.make#2`)."""
    path, colon, member = query.partition(":")
    found = []
    for module in _module_ids(graph, path):
        if not colon:
            found.append(module)
            continue
        wanted = f"{module}:{member}"
        found += [sid for sid in graph.symbols if sid == wanted or _CLASH.sub("", sid) == wanted]
    return found


def _matches(graph: Graph, query: str) -> list[str]:
    """Every id the query names. At a dot it ends an id (Python, and a TypeScript member). A query with a
    path in it (a slash, a colon, a TypeScript extension) goes through the file map (_path_matches); any
    other also ends an id at a slash or a colon (a bare TypeScript member or module name), with or
    without a clash suffix."""
    path_shaped = ":" in query or "/" in query or query.endswith(SUFFIXES)
    found = set()
    for sid in graph.symbols:
        if sid.endswith("." + query):
            found.add(sid)
        elif not path_shaped and _is_path_id(sid):
            forms = {sid, _CLASH.sub("", sid)}
            if any(f == query or f.endswith(sep + query) for f in forms for sep in ".:/"):
                found.add(sid)
    if path_shaped:
        found.update(_path_matches(graph, query))
    return sorted(found)


def _tail(query: str) -> str:
    """The part of a query to compare with symbol names when suggesting: the last member or path segment."""
    if ":" in query:
        return query.rpartition(":")[2].rsplit(".", 1)[-1]
    if "/" in query:
        return module_id(query).rsplit("/", 1)[-1]
    return query.rsplit(".", 1)[-1]


def find_symbol(graph: Graph, query: str) -> dict:
    """{"symbol": id} on a unique match, else {"error", "candidates" | "suggestions"}.

    A Python id is dotted (pkg.mod.Class.method) and a suffix at a dot names it. A TypeScript id is a
    path and a member (src/util/math:clamp): it is also named by the path with its extension, by the
    tail of the path (util/math:clamp), by Class.method or by the bare member, whichever of them
    is only one symbol's. More than one symbol is never picked between: they are listed.
    """
    query = query.strip()
    found = _find(graph, query)
    if found.get("symbol") in graph.ambiguous_ids:
        files = ", ".join(graph.ambiguous_ids[found["symbol"]])
        return {
            "error": f"both languages define '{found['symbol']}' ({files}), so ripple cannot tell their callers "
            "apart and does not answer for it; ask about a symbol inside one of the files",
        }
    return found


def _find(graph: Graph, query: str) -> dict:
    if query in graph.symbols:
        return {"symbol": query}
    matches = _matches(graph, query)
    if len(matches) == 1:
        return {"symbol": matches[0]}
    if matches:
        return {"error": f"'{query}' is ambiguous; pass one of the candidates", "candidates": matches}
    names = {s.name: s.id for s in graph.symbols.values()}
    close = difflib.get_close_matches(_tail(query), list(names), n=5)
    return {"error": f"no symbol named '{query}'", "suggestions": [names[n] for n in close]}


def _describe(symbol: Symbol) -> dict:
    return {"symbol": symbol.id, "kind": symbol.kind, "location": f"{symbol.file}:{symbol.line}"}


def _profile_of(symbol: Symbol, profile: LanguageProfile | None) -> LanguageProfile:
    """The profile a query uses for one symbol: the one it was given, or (None) the language of the
    symbol's file, so one graph can hold Python and TypeScript and each is read by its own rules."""
    if profile is not None:
        return profile
    return TYPESCRIPT if symbol.file.endswith(TYPESCRIPT.source_suffixes) else PYTHON


class _Collections:
    """The test collection of each language in the graph, built when first asked, and asked per symbol."""

    def __init__(self, graph: Graph, profile: LanguageProfile | None):
        self.graph, self.profile = graph, profile
        self.built: dict[str, object] = {}

    def node_ids(self, symbol_id: str) -> list[str]:
        profile = _profile_of(self.graph.symbols[symbol_id], self.profile)
        if profile.name not in self.built:
            self.built[profile.name] = profile.test_collection(self.graph)
        return self.built[profile.name].node_ids(symbol_id)


def _targets(graph: Graph, symbol_id: str, profile: LanguageProfile | None) -> set[str]:
    """Ids whose callers count as callers of symbol_id.

    Calling a class runs its constructor (__init__ in Python), and ripple records that edge against
    the constructor, so a question about the class includes both.
    """
    targets = {symbol_id}
    constructor = f"{symbol_id}.{_profile_of(graph.symbols[symbol_id], profile).constructor_name}"
    if graph.symbols[symbol_id].kind == "class" and constructor in graph.symbols:
        targets.add(constructor)
    return targets


def _continues(edge: Edge, context: str) -> bool:
    """Whether a chain that went through a wrapper for the decorated function `context` (empty: no
    particular one) may go on along this edge into that wrapper.

    One wrapper body serves every function its decorator is applied to, so the callers of one
    decorated function are not callers of another through it: an edge that belongs to a
    different decoration (see Edge.via) is not part of the chain.
    """
    return not (context and edge.via and edge.via != context)


def callers(graph: Graph, query: str, profile: LanguageProfile | None = None) -> dict:
    found = find_symbol(graph, query)
    if "symbol" not in found:
        return found
    sites: dict[str, list[str]] = {}
    for target in _targets(graph, found["symbol"], profile):
        for edge in graph.callers_index.get(target, []):
            sites.setdefault(edge.caller, []).append(f"{edge.file}:{edge.line}")
    listed = [
        {**_describe(graph.symbols[caller]), "call_sites": sorted(set(lines))}
        for caller, lines in sorted(sites.items())
    ]
    # A decorated function is called by its wrapper. The lines that write its name call the wrapper, and
    # reach the function through it: list them too, since changing the function changes what they run.
    through: dict[str, tuple[str, list[str]]] = {}
    for target in _targets(graph, found["symbol"], profile):
        for edge in graph.via_index.get(target, []):
            through.setdefault(edge.caller, (edge.callee, []))[1].append(f"{edge.file}:{edge.line}")
    reaching = [
        {**_describe(graph.symbols[caller]), "call_sites": sorted(set(lines)), "via_wrapper": wrapper}
        for caller, (wrapper, lines) in sorted(through.items())
    ]
    return {"symbol": found["symbol"], "count": len(listed), "callers": listed, "through_wrapper": reaching}


def callees(graph: Graph, query: str) -> dict:
    found = find_symbol(graph, query)
    if "symbol" not in found:
        return found
    symbol_id = found["symbol"]
    sites: dict[str, list[str]] = {}
    for edge in graph.callees_index.get(symbol_id, []):
        sites.setdefault(edge.callee, []).append(f"{edge.file}:{edge.line}")
    listed = [
        {**_describe(graph.symbols[callee]), "call_sites": sorted(lines)} for callee, lines in sorted(sites.items())
    ]
    unresolved = [
        {"call": u.text, "location": f"{u.file}:{u.line}", "reason": u.reason}
        for u in graph.unresolved
        if u.caller == symbol_id
    ]
    return {"symbol": symbol_id, "callees": listed, "unresolved": unresolved}


def impact(graph: Graph, query: str, depth: int = 5, limit: int = MAX_LISTED, profile: LanguageProfile | None = None) -> dict:
    """Everything that transitively calls the symbol, up to depth hops away.

    Tests that reach it are listed with their pytest node ids (what you
    should run, and what check accepts); a test method that several test
    classes inherit is listed once per class. Every other caller, including
    helpers in test files, is grouped by file (what you may need to update).
    Each list holds at most limit entries; truncated and the omitted_* counts
    say when more were found. possible_missed_callers are call sites ripple
    could not resolve that use the same name: static analysis cannot prove
    they reach the symbol, but a human should look.
    """
    found = find_symbol(graph, query)
    if "symbol" not in found:
        return found
    symbol_id = found["symbol"]
    targets = _targets(graph, symbol_id, profile)
    distance: dict[str, int] = {}
    via_wrapper: dict[str, str] = {}  # caller -> the wrapper it reached the symbol through, when it names a decorated function
    seen = {(target, "") for target in targets}
    frontier = set(seen)
    for hop in range(1, depth + 1):
        next_frontier = set()
        for target, context in frontier:
            for edge in graph.callers_index.get(target, []):
                if edge.caller in targets or not _continues(edge, context):
                    continue
                state = (edge.caller, edge.via if edge.kind == "wrapped" else "")
                if state not in seen:
                    seen.add(state)
                    if edge.caller not in distance:
                        distance[edge.caller] = hop
                        if edge.via and edge.kind != "wrapped":
                            via_wrapper[edge.caller] = edge.callee
                    next_frontier.add(state)
        frontier = next_frontier
    more_beyond_depth = any(
        edge.caller not in distance and edge.caller not in targets and _continues(edge, context)
        for target, context in frontier
        for edge in graph.callers_index.get(target, [])
    )

    collection = _Collections(graph, profile)
    code: list[dict] = []
    tests: list[dict] = []
    for sid in sorted(distance, key=lambda sid: (distance[sid], sid)):
        symbol = graph.symbols[sid]
        entry = {"symbol": sid, "line": symbol.line, "distance": distance[sid]}
        if sid in via_wrapper:
            entry["via_wrapper"] = via_wrapper[sid]
        node_ids = collection.node_ids(sid)
        for node_id in node_ids:
            tests.append({"node_id": node_id, **entry, "file": node_id.split("::")[0]})
        if not node_ids:
            code.append({**entry, "file": symbol.file})
    by_file: dict[str, list[dict]] = {}
    for entry in code[:limit]:
        by_file.setdefault(entry.pop("file"), []).append(entry)

    name = graph.symbols[symbol_id].name
    missed = [
        {"caller": u.caller, "call": u.text, "location": f"{u.file}:{u.line}"}
        for u in graph.unresolved
        if u.text.rsplit(".", 1)[-1] == name
    ]
    omitted_callers, omitted_tests = max(0, len(code) - limit), max(0, len(tests) - limit)
    return {
        "symbol": symbol_id,
        "depth": depth,
        "affected": len(distance),
        "more_beyond_depth": more_beyond_depth,
        "by_file": by_file,
        "caller_count": len(code),
        "tests": tests[:limit],
        "test_count": len(tests),
        "truncated": bool(omitted_callers or omitted_tests),
        "omitted_callers": omitted_callers,
        "omitted_tests": omitted_tests,
        "possible_missed_callers": missed[:20],
        "possible_missed_total": len(missed),
    }


def path(graph: Graph, source: str, target: str, max_depth: int = 12, profile: LanguageProfile | None = None) -> dict:
    """The shortest call chain from source to target (breadth-first search)."""
    start, goal = find_symbol(graph, source), find_symbol(graph, target)
    for found in (start, goal):
        if "symbol" not in found:
            return found
    goals = _targets(graph, goal["symbol"], profile)
    # A step is (symbol, the decorated function whose wrapper the chain has just entered, if any).
    first = (start["symbol"], "")
    came_from: dict[tuple[str, str], tuple[tuple[str, str], str] | None] = {first: None}
    queue = deque([(first, 0)])
    end = first if first[0] in goals else None
    while queue and end is None:
        current, hops = queue.popleft()
        if hops >= max_depth:
            continue
        for edge in graph.callees_index.get(current[0], []):
            if edge.kind == "wrapped" and not _continues(edge, current[1]):
                continue  # this wrapper was entered for another decorated function
            step = (edge.callee, edge.via)
            if step not in came_from:
                came_from[step] = (current, f"{edge.file}:{edge.line}")
                if edge.callee in goals:
                    end = step
                    break
                queue.append((step, hops + 1))
    result = {"from": start["symbol"], "to": goal["symbol"], "path": None}
    if end is None:
        return result
    chain, node = [], end
    while node is not None:
        step = came_from[node]
        chain.append({**_describe(graph.symbols[node[0]]), "called_at": step[1] if step else None})
        node = step[0] if step else None
    result["path"] = chain[::-1]
    return result
