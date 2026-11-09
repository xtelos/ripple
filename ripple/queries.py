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

from .jsx_facts import JSX_NO_RENDERER, JSX_TAG_UNRESOLVED
from .model import DECORATOR_SCOPE, UNREAD_CLASS, Decoration, Edge, Graph, Symbol, Unresolved
from .profile import PYTHON, TYPESCRIPT, LanguageProfile
from .pytest_collection import PytestCollection  # noqa: F401 (it was defined here)
from .ts_modules import SUFFIXES, module_id  # no parser needed to read an id

MAX_LISTED = 100  # default cap on symbols listed per section, so a hub function cannot flood the agent


_CLASH = re.compile(r"#\d+$")

# The kinds of edge a question can be about. `calls` is every edge a call makes (a call, `new`, an override, a
# callback, a decorator); `renders` and `renderer` are the two JSX edges (see ts_indexer.py and jsx.py).
KINDS = ("calls", "renders", "renderer")
JSX_KINDS = ("renders", "renderer")


def parse_kinds(value: str | None) -> frozenset[str] | None:
    """The kinds a `--kinds calls,renders` or a tool's `kinds` argument names, None for all.
    Raises ValueError for a word that is none."""
    if value is None:
        return None
    words = [w.strip().lower() for w in value.split(",") if w.strip()]
    if not words or "all" in words:
        return None
    unknown = [w for w in words if w not in KINDS]
    if unknown:
        raise ValueError(f"unknown edge kind {unknown[0]!r}: choose from {', '.join(KINDS)}, or all")
    return frozenset(words)


def edge_group(edge: Edge) -> str:
    """The kind a question filters on: renders, renderer, else calls."""
    return edge.kind if edge.kind in JSX_KINDS else "calls"


def _unresolved_group(u: Unresolved) -> str:
    if u.reason.startswith(JSX_NO_RENDERER):
        return "renderer"
    return "renders" if u.reason.startswith(JSX_TAG_UNRESOLVED) else "calls"


def _wanted(group: str, kinds) -> bool:
    return kinds is None or group in kinds


def _edge_kinds(groups) -> list[str] | None:
    """The labelled kinds of a set of edges: None when none is a JSX edge (a plain call carries no label)."""
    found = [k for k in KINDS if k in groups]
    return found if any(k in JSX_KINDS for k in found) else None


DECORATION_NOTE = (
    "a decorator is applied when the class it is written on is defined. These are not calls, and what a decorator "
    "does to what it decorates is not modelled, so changing this symbol can change each of them"
)


def _decoration_entry(d: Decoration) -> dict:
    return {"in": d.holder, "applied_at": f"{d.file}:{d.line}", "written": d.text}


def _decorations(graph: Graph, symbol_id: str, targets: set[str], kinds, limit: int = MAX_LISTED) -> dict:
    """The places the symbol is written as a decorator, apart from its callers: `decorations` where the name resolved
    to it, `possible_decorations` where a name that did not resolve ends the way its own does (a module is never
    matched by name), each with where it is written and the note. Empty when there are none, so a symbol that is
    never a decorator (and every Python symbol) is answered with exactly the keys it always had."""
    if not graph.decorations or not _wanted("calls", kinds):
        return {}
    symbol = graph.symbols[symbol_id]
    order = lambda d: (d.file, d.line, d.holder)  # noqa: E731
    proven = sorted((d for d in graph.decorations if d.target in targets), key=order)
    possible = (
        []
        if symbol.kind == "module"
        else sorted((d for d in graph.decorations if not d.target and d.name == symbol.name and _same_language(d.file, symbol)), key=order)
    )
    found: dict = {}
    if proven:
        found["decorations"] = [_decoration_entry(d) for d in proven[:limit]]
    if possible:
        found["possible_decorations"] = [_decoration_entry(d) for d in possible[:limit]]
    if found:
        found["decoration_note"] = DECORATION_NOTE
        omitted = max(0, len(proven) - limit) + max(0, len(possible) - limit)
        if omitted:
            found["decorations_omitted"] = omitted
    return found


DECORATIONS_OF_CALLERS_NOTE = (
    "these callers are written as decorators at these sites, and a decorator is applied when the class it is written on "
    "is defined. The sites are not calls and are not followed: what each decorator does to what it decorates is not "
    "modelled, so the reach of the change continues beyond them. Only sites where the name resolved to the caller are "
    "listed"
)


def _decorations_of_callers(graph: Graph, distance: dict[str, int], kinds, profile, limit: int) -> dict:
    """Where the callers impact found are themselves applied as decorators. A caller that is a decorator runs, through
    each site, whenever the class it decorates is defined, so the reach of a change does not end at it. The decorated
    holders are not callers (never an edge, so not followed), and a decoration only by name is not listed: this is a
    flag that the reach continues, not a second traversal. A class that is a decorator is found through its
    constructor, the symbol a call of it reaches. Empty when there are none (and for every Python symbol)."""
    if not graph.decorations or not distance or not _wanted("calls", kinds):
        return {}
    applied: dict[str, list[str]] = {}  # decoration target -> the affected symbols that are that decorator
    for sid in distance:
        applied.setdefault(sid, []).append(sid)
        suffix = "." + _profile_of(graph.symbols[sid], profile).constructor_name
        owner = sid[: -len(suffix)] if sid.endswith(suffix) else ""
        if owner in graph.symbols and graph.symbols[owner].kind == "class":
            applied.setdefault(owner, []).append(sid)
    sites = sorted(
        ((d, sid) for d in graph.decorations for sid in applied.get(d.target, ())),
        key=lambda found: (distance[found[1]], found[1], found[0].file, found[0].line, found[0].holder),
    )
    if not sites:
        return {}
    found = {
        "decorations_of_callers": [
            {**_decoration_entry(d), "decorator": sid, "distance": distance[sid]} for d, sid in sites[:limit]
        ],
        "decorations_of_callers_note": DECORATIONS_OF_CALLERS_NOTE,
    }
    if len(sites) > limit:
        found["decorations_of_callers_omitted"] = len(sites) - limit
    return found


_MISSED_REASONS = (UNREAD_CLASS, DECORATOR_SCOPE)


def _same_language(file: str, symbol: Symbol) -> bool:
    """Whether a file (where a decorator or a call is written) is in the language of the symbol's own file. Only
    TypeScript front end reads decorators and class expressions, so a Python symbol is never the target of one."""
    return file.endswith(TYPESCRIPT.source_suffixes) == symbol.file.endswith(TYPESCRIPT.source_suffixes)


def _missed(u: Unresolved) -> dict:
    """A same-name unresolved call as a question lists it. One inside a class ripple does not read says so."""
    entry = {"caller": u.caller, "call": u.text, "location": f"{u.file}:{u.line}"}
    return {**entry, "reason": u.reason} if u.reason in _MISSED_REASONS else entry


def _unread_class_calls(graph: Graph, symbol_id: str, kinds) -> dict:
    """The calls written inside classes ripple does not read that end with the symbol's name, for a question that
    lists no unresolved calls of its own (callers). Only these: any other same-name call is impact's to list. A
    module is never matched by name."""
    symbol = graph.symbols[symbol_id]
    if symbol.kind == "module" or not _wanted("calls", kinds):
        return {}
    found = [
        _missed(u)
        for u in graph.unresolved
        if u.reason in _MISSED_REASONS and u.text.rsplit(".", 1)[-1] == symbol.name and _same_language(u.file, symbol)
    ]
    return {"possible_missed_callers": found[:20], "possible_missed_total": len(found)} if found else {}


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


def callers(graph: Graph, query: str, profile: LanguageProfile | None = None, kinds=None) -> dict:
    found = find_symbol(graph, query)
    if "symbol" not in found:
        return found
    sites: dict[str, list[tuple[str, str]]] = {}
    for target in _targets(graph, found["symbol"], profile):
        for edge in graph.callers_index.get(target, []):
            if _wanted(edge_group(edge), kinds):
                sites.setdefault(edge.caller, []).append((f"{edge.file}:{edge.line}", edge_group(edge)))
    listed = [{**_describe(graph.symbols[caller]), **_sites(here)} for caller, here in sorted(sites.items())]
    # A decorated function is called by its wrapper. The lines that write its name call the wrapper, and
    # reach the function through it: list them too, since changing the function changes what they run.
    through: dict[str, tuple[str, list[str]]] = {}
    for target in _targets(graph, found["symbol"], profile):
        for edge in graph.via_index.get(target, []):
            if _wanted(edge_group(edge), kinds):
                through.setdefault(edge.caller, (edge.callee, []))[1].append(f"{edge.file}:{edge.line}")
    reaching = [
        {**_describe(graph.symbols[caller]), "call_sites": sorted(set(lines)), "via_wrapper": wrapper}
        for caller, (wrapper, lines) in sorted(through.items())
    ]
    result = {"symbol": found["symbol"], "count": len(listed), "callers": listed, "through_wrapper": reaching}
    result.update(_unread_class_calls(graph, found["symbol"], kinds))
    result.update(_decorations(graph, found["symbol"], _targets(graph, found["symbol"], profile), kinds))
    return result


def _sites(found: list[tuple[str, str]], unique: bool = True) -> dict:
    """An entry's `call_sites` (a callee lists a site once for each call written there), and when one of its edges is a
    JSX edge, `edge_kinds` and `sites_by_kind` (the sites of each kind)."""
    entry: dict = {"call_sites": sorted({site for site, _ in found} if unique else [site for site, _ in found])}
    labelled = _edge_kinds({group for _, group in found})
    if labelled:
        entry["edge_kinds"] = labelled
        entry["sites_by_kind"] = {k: sorted({site for site, group in found if group == k}) for k in labelled}
    return entry


def callees(graph: Graph, query: str, kinds=None) -> dict:
    found = find_symbol(graph, query)
    if "symbol" not in found:
        return found
    symbol_id = found["symbol"]
    sites: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.callees_index.get(symbol_id, []):
        if _wanted(edge_group(edge), kinds):
            sites.setdefault(edge.callee, []).append((f"{edge.file}:{edge.line}", edge_group(edge)))
    listed = [{**_describe(graph.symbols[callee]), **_sites(here, unique=False)} for callee, here in sorted(sites.items())]
    unresolved = [
        {"call": u.text, "location": f"{u.file}:{u.line}", "reason": u.reason}
        for u in graph.unresolved
        if u.caller == symbol_id and _wanted(_unresolved_group(u), kinds)
    ]
    result = {"symbol": symbol_id, "callees": listed, "unresolved": unresolved}
    applied = [
        {"decorator": d.target or None, "applied_at": f"{d.file}:{d.line}", "written": d.text}
        for d in graph.decorations
        if d.holder == symbol_id and _wanted("calls", kinds)
    ]
    if applied:  # not callees: the decorator is named only when its name resolved, and what it does is not modelled
        result["applies_decorators"] = applied
    return result


def impact(
    graph: Graph, query: str, depth: int = 5, limit: int = MAX_LISTED, profile: LanguageProfile | None = None, kinds=None
) -> dict:
    """Everything that transitively calls the symbol, up to depth hops away.

    Tests that reach it are listed with their pytest node ids (what you
    should run, and what check accepts); a test method that several test
    classes inherit is listed once per class. Every other caller, including
    helpers in test files, is grouped by file (what you may need to update).
    Each list holds at most limit entries; truncated and the omitted_* counts
    say when more were found. possible_missed_callers are call sites ripple
    could not resolve that use the same name: static analysis cannot prove
    they reach the symbol, but a human should look.

    kinds (None: all) says which edges are followed: `calls`, `renders` (a function that writes <Symbol />, so
    changing the symbol can break it) and `renderer` (the component React runs it inside). A caller reached through
    a JSX edge carries edge_kinds, the kinds of the edges that reached it.
    """
    found = find_symbol(graph, query)
    if "symbol" not in found:
        return found
    symbol_id = found["symbol"]
    targets = _targets(graph, symbol_id, profile)
    distance: dict[str, int] = {}
    via_wrapper: dict[str, str] = {}  # caller -> the wrapper it reached the symbol through, when it names a decorated function
    reached: dict[str, set[str]] = {}  # caller -> the kinds of the edges that reached it at its distance
    seen = {(target, "") for target in targets}
    frontier = set(seen)
    for hop in range(1, depth + 1):
        next_frontier = set()
        for target, context in frontier:
            for edge in graph.callers_index.get(target, []):
                if edge.caller in targets or not _continues(edge, context) or not _wanted(edge_group(edge), kinds):
                    continue
                if distance.get(edge.caller, hop) == hop:
                    reached.setdefault(edge.caller, set()).add(edge_group(edge))
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
        edge.caller not in distance
        and edge.caller not in targets
        and _continues(edge, context)
        and _wanted(edge_group(edge), kinds)
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
        labelled = _edge_kinds(reached.get(sid, ()))
        if labelled:
            entry["edge_kinds"] = labelled
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
        _missed(u)
        for u in graph.unresolved
        if u.text.rsplit(".", 1)[-1] == name
        and _unresolved_group(u) != "renderer"
        and _wanted(_unresolved_group(u), kinds)
        and (u.reason not in _MISSED_REASONS or _same_language(u.file, graph.symbols[symbol_id]))
    ]
    omitted_callers, omitted_tests = max(0, len(code) - limit), max(0, len(tests) - limit)
    result = {
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
    result.update(_decorations(graph, symbol_id, targets, kinds, limit))
    result.update(_decorations_of_callers(graph, distance, kinds, profile, limit))
    if result.get("decorations_omitted") or result.get("decorations_of_callers_omitted"):
        result["truncated"] = True  # a decoration site left out is a thing found and not listed, as a caller is
    return result


def path(
    graph: Graph, source: str, target: str, max_depth: int = 12, profile: LanguageProfile | None = None, kinds=None
) -> dict:
    """The shortest call chain from source to target (breadth-first search), along the kinds of edge asked for
    (None: all). A hop that is a JSX edge carries edge_kinds, the kinds of the edges between its two symbols."""
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
            if not _wanted(edge_group(edge), kinds):
                continue
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
    result.update(_decorations(graph, goal["symbol"], goals, kinds))  # a chain to it is not the only way it is reached
    if end is None:
        return result
    chain, node = [], end
    while node is not None:
        step = came_from[node]
        hop = {**_describe(graph.symbols[node[0]]), "called_at": step[1] if step else None}
        if step:
            between = {
                edge_group(e)
                for e in graph.callees_index.get(step[0][0], [])
                if e.callee == node[0] and _wanted(edge_group(e), kinds)
            }
            labelled = _edge_kinds(between)
            if labelled:
                hop["edge_kinds"] = labelled
        chain.append(hop)
        node = step[0] if step else None
    result["path"] = chain[::-1]
    return result
