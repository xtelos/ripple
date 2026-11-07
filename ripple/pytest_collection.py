"""Which symbols pytest would collect as tests, and under which node ids.

This is the Python profile's answer to "which tests run this symbol" (see
profile.py): another language's test runner gets its own class with the same
two methods the query layer calls, __init__(graph) and node_ids(symbol_id).
"""

from __future__ import annotations

from pathlib import PurePosixPath

from .model import Graph, Symbol


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
