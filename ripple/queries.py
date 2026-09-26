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


class PytestCollection:
    """Which symbols pytest would collect as tests, and under which node ids.

    This follows pytest's default rules. In files named test_*.py or
    *_test.py it collects functions named test* at module level; methods
    named test* on classes named Test* that define no __init__ or __new__
    (nested Test* classes too); and every test* method of a
    unittest.TestCase subclass, whatever the class is called. A class also
    runs the test* methods it inherits, so a method defined once can be
    collected as several tests: TestChild(TestBase) runs
    TestChild::test_shared as well as TestBase::test_shared.

    ripple sees only the repo's own classes, so a class counts as a TestCase
    subclass when a base outside the repo is named *TestCase
    (unittest.TestCase, unittest.IsolatedAsyncioTestCase,
    django.test.TestCase and so on).
    """

    def __init__(self, graph: Graph):
        self.graph = graph
        self.subclasses: dict[str, list[str]] = {}
        for cls, bases in graph.bases.items():
            for base in bases:
                self.subclasses.setdefault(base, []).append(cls)
        self._mro: dict[str, list[str]] = {}

    def module_of(self, symbol: Symbol) -> str:
        """The module a symbol was defined in, looked up by its file.

        Two files can map to the same module name (tests/test_x.py and
        src/tests/test_x.py), so the module symbol alone cannot say which
        file a symbol came from; the file can.
        """
        if symbol.file in self.graph.modules:
            return self.graph.modules[symbol.file]
        prefix = symbol.id
        while "." in prefix:  # a graph without the file map: the longest prefix that is a module
            prefix = prefix.rsplit(".", 1)[0]
            if self.graph.symbols.get(prefix) and self.graph.symbols[prefix].kind == "module":
                return prefix
        return prefix

    def mro(self, cls: str) -> list[str]:
        """The class, then its repo bases depth-first, as the resolver orders them."""
        if cls not in self._mro:
            self._mro[cls] = [cls]  # guards against a class that inherits itself
            order = [cls]
            for base in self.graph.bases.get(cls, []):
                order += [klass for klass in self.mro(base) if klass not in order]
            self._mro[cls] = order
        return self._mro[cls]

    def lookup(self, cls: str, name: str) -> str | None:
        """The id of the def that cls.name finds, walking bases."""
        for klass in self.mro(cls):
            if f"{klass}.{name}" in self.graph.symbols:
                return f"{klass}.{name}"
        return None

    def is_unittest(self, cls: str) -> bool:
        return any(
            name.rsplit(".", 1)[-1].endswith("TestCase")
            for klass in self.mro(cls)
            for name in self.graph.external_bases.get(klass, [])
        )

    def is_pytest_class(self, cls: str) -> bool:
        """A plain Test* class that pytest can instantiate."""
        if not cls.rsplit(".", 1)[-1].startswith("Test") or self.is_unittest(cls):
            return False
        return not any(self.lookup(cls, dunder) for dunder in ("__init__", "__new__"))

    def class_path(self, cls: str) -> list[str] | None:
        """[file, Outer, ..., cls] if pytest collects cls, else None."""
        symbol = self.graph.symbols[cls]
        filename = PurePosixPath(symbol.file).name
        if not (filename.startswith("test_") or filename.endswith("_test.py")):
            return None
        if not (self.is_pytest_class(cls) or self.is_unittest(cls)):
            return None
        module = self.module_of(symbol)
        if not cls.startswith(module + "."):
            return None
        parts = cls[len(module) + 1 :].split(".")
        enclosing = module
        for name in parts[:-1]:  # pytest looks for classes inside Test* classes, not inside TestCases or functions
            enclosing = f"{enclosing}.{name}"
            if self.graph.symbols[enclosing].kind != "class" or not self.is_pytest_class(enclosing):
                return None
        return [symbol.file] + parts

    def collecting_classes(self, cls: str) -> list[str]:
        """cls and every class that inherits from it, transitively."""
        found, stack = [], [cls]
        while stack:
            klass = stack.pop()
            if klass not in found:
                found.append(klass)
                stack.extend(self.subclasses.get(klass, []))
        return found

    def node_ids(self, symbol_id: str) -> list[str]:
        """The pytest node ids that run symbol_id, e.g. tests/test_x.py::TestA::test_m.

        Empty when pytest would not collect it. A method inherited by other
        test classes gets one node id per class that runs it, named after
        that class rather than the base that defines it.
        """
        symbol = self.graph.symbols[symbol_id]
        if not symbol.name.startswith("test"):
            return []
        if symbol.kind == "function":
            filename = PurePosixPath(symbol.file).name
            if not (filename.startswith("test_") or filename.endswith("_test.py")):
                return []
            if symbol_id != f"{self.module_of(symbol)}.{symbol.name}":
                return []  # nested in a function, not at module level
            return [f"{symbol.file}::{symbol.name}"]
        if symbol.kind != "method":
            return []
        owner = symbol_id.rsplit(".", 1)[0]
        ids = []
        for cls in self.collecting_classes(owner):
            if self.lookup(cls, symbol.name) != symbol_id:
                continue  # cls overrides it, or finds another definition first
            where = self.class_path(cls)
            if where:
                ids.append("::".join(where + [symbol.name]))
        return sorted(ids)


def impact(graph: Graph, query: str, depth: int = 5, limit: int = MAX_LISTED) -> dict:
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

    collection = PytestCollection(graph)
    code: list[dict] = []
    tests: list[dict] = []
    for sid in sorted(distance, key=lambda sid: (distance[sid], sid)):
        symbol = graph.symbols[sid]
        entry = {"symbol": sid, "line": symbol.line, "distance": distance[sid]}
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
