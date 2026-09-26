"""The questions an agent asks before editing: who calls this, what does it
call, what could break, and how does A reach B.

Every function takes a symbol as the user typed it (a full dotted path, a
Class.method suffix, or a bare name) and returns a plain dict ready to be
serialized. When the name is ambiguous or unknown, the dict says so and lists
candidates instead of picking one.
"""

from __future__ import annotations

import difflib
from collections import deque
from pathlib import PurePosixPath

from .model import Graph, Symbol

MAX_LISTED = 100  # default cap on symbols listed per section, so a hub function cannot flood the agent


def find_symbol(graph: Graph, query: str) -> dict:
    """{"symbol": id} on a unique match, else {"error", "candidates" | "suggestions"}."""
    query = query.strip()
    if query in graph.symbols:
        return {"symbol": query}
    matches = sorted(sid for sid in graph.symbols if sid.endswith("." + query))
    if len(matches) == 1:
        return {"symbol": matches[0]}
    if matches:
        return {"error": f"'{query}' is ambiguous; pass one of the candidates", "candidates": matches}
    names = {s.name: s.id for s in graph.symbols.values()}
    close = difflib.get_close_matches(query.rsplit(".", 1)[-1], list(names), n=5)
    return {"error": f"no symbol named '{query}'", "suggestions": [names[n] for n in close]}


def _describe(symbol: Symbol) -> dict:
    return {"symbol": symbol.id, "kind": symbol.kind, "location": f"{symbol.file}:{symbol.line}"}


def _targets(graph: Graph, symbol_id: str) -> set[str]:
    """Ids whose callers count as callers of symbol_id.

    Calling a class runs its __init__, and ripple records that edge against
    __init__, so a question about the class includes both.
    """
    targets = {symbol_id}
    if graph.symbols[symbol_id].kind == "class" and f"{symbol_id}.__init__" in graph.symbols:
        targets.add(f"{symbol_id}.__init__")
    return targets


def callers(graph: Graph, query: str) -> dict:
    found = find_symbol(graph, query)
    if "symbol" not in found:
        return found
    sites: dict[str, list[str]] = {}
    for target in _targets(graph, found["symbol"]):
        for edge in graph.callers_index.get(target, []):
            sites.setdefault(edge.caller, []).append(f"{edge.file}:{edge.line}")
    listed = [
        {**_describe(graph.symbols[caller]), "call_sites": sorted(lines)}
        for caller, lines in sorted(sites.items())
    ]
    return {"symbol": found["symbol"], "count": len(listed), "callers": listed}


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


def pytest_node_id(graph: Graph, symbol_id: str, module_by_file: dict[str, str]) -> str | None:
    """The pytest node id that runs a test, e.g. tests/test_x.py::TestA::test_m.

    None when pytest would not collect the symbol as a test. This follows
    pytest's default rules: files named test_*.py or *_test.py, functions
    named test* at module level, and test* methods of classes named Test*.
    """
    symbol = graph.symbols[symbol_id]
    filename = PurePosixPath(symbol.file).name
    if not (filename.startswith("test_") or filename.endswith("_test.py")):
        return None
    if symbol.kind not in ("function", "method") or not symbol.name.startswith("test"):
        return None
    module = module_by_file[symbol.file]
    parts = symbol_id[len(module) + 1 :].split(".")
    enclosing = module
    for name in parts[:-1]:  # everything between the module and the test must be a Test* class
        enclosing = f"{enclosing}.{name}"
        if graph.symbols[enclosing].kind != "class" or not name.startswith("Test"):
            return None
    return "::".join([symbol.file] + parts)


def impact(graph: Graph, query: str, depth: int = 5, limit: int = MAX_LISTED) -> dict:
    """Everything that transitively calls the symbol, up to depth hops away.

    Tests that reach it are listed with their pytest node ids (what you
    should run, and what check accepts). Every other caller, including
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
    targets = _targets(graph, symbol_id)
    distance: dict[str, int] = {}
    frontier = set(targets)
    for hop in range(1, depth + 1):
        next_frontier = set()
        for target in frontier:
            for edge in graph.callers_index.get(target, []):
                if edge.caller not in distance and edge.caller not in targets:
                    distance[edge.caller] = hop
                    next_frontier.add(edge.caller)
        frontier = next_frontier
    more_beyond_depth = any(
        edge.caller not in distance and edge.caller not in targets
        for target in frontier
        for edge in graph.callers_index.get(target, [])
    )

    module_by_file = {s.file: s.id for s in graph.symbols.values() if s.kind == "module"}
    code: list[dict] = []
    tests: list[dict] = []
    for sid in sorted(distance, key=lambda sid: (distance[sid], sid)):
        symbol = graph.symbols[sid]
        entry = {"symbol": sid, "line": symbol.line, "distance": distance[sid]}
        node_id = pytest_node_id(graph, sid, module_by_file)
        if node_id:
            tests.append({"node_id": node_id, **entry, "file": symbol.file})
        else:
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


def path(graph: Graph, source: str, target: str, max_depth: int = 12) -> dict:
    """The shortest call chain from source to target (breadth-first search)."""
    start, goal = find_symbol(graph, source), find_symbol(graph, target)
    for found in (start, goal):
        if "symbol" not in found:
            return found
    goals = _targets(graph, goal["symbol"])
    came_from: dict[str, tuple[str, str] | None] = {start["symbol"]: None}
    queue = deque([(start["symbol"], 0)])
    end = start["symbol"] if start["symbol"] in goals else None
    while queue and end is None:
        current, hops = queue.popleft()
        if hops >= max_depth:
            continue
        for edge in graph.callees_index.get(current, []):
            if edge.callee not in came_from:
                came_from[edge.callee] = (current, f"{edge.file}:{edge.line}")
                if edge.callee in goals:
                    end = edge.callee
                    break
                queue.append((edge.callee, hops + 1))
    result = {"from": start["symbol"], "to": goal["symbol"], "path": None}
    if end is None:
        return result
    chain, node = [], end
    while node is not None:
        step = came_from[node]
        chain.append({**_describe(graph.symbols[node]), "called_at": step[1] if step else None})
        node = step[0] if step else None
    result["path"] = chain[::-1]
    return result
