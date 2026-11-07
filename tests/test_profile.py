"""The language profile: what the resolver, queries, checker and cache assume about Python.

PYTHON must hold the values those modules used to hard-code, and each of them must
read its profile from the parameter, not from a module constant, so that a profile
for another language changes what they do.
"""

import builtins
import sys
from dataclasses import replace

import pytest

from ripple import facts
from ripple.cache import fingerprint
from ripple.checker import default_test_command
from ripple.indexer import build_graph, find_source_files
from ripple.profile import PYTHON
from ripple.queries import callers, impact
from ripple.resolver import Resolver


def test_the_python_profile_holds_what_the_modules_used_to_hard_code():
    assert PYTHON.source_suffixes == (".py",)
    assert PYTHON.builtin_names == set(dir(builtins))
    assert PYTHON.builtin_types == {f"builtins.{n}" for n in dir(builtins) if isinstance(getattr(builtins, n), type)}
    assert {"builtins.list", "builtins.dict", "builtins.type", "builtins.super"} <= PYTHON.builtin_types
    assert "builtins.len" not in PYTHON.builtin_types
    assert PYTHON.not_built_as_instance == {"builtins.type", "builtins.super"}
    assert len(PYTHON.safe_import_modules) == 56
    assert {"threading", "logging.handlers", "xml.etree.ElementTree"} <= PYTHON.safe_import_modules
    assert not {"this", "antigravity", "tkinter", "idlelib"} & PYTHON.safe_import_modules
    assert PYTHON.stdlib_module_names == sys.stdlib_module_names
    assert PYTHON.nominal_only_excluded == ("typing.", "typing_extensions.", "collections.abc.", "abc.")
    assert len(PYTHON.same_class_decorators) == 11 and "dataclasses.dataclass" in PYTHON.same_class_decorators
    assert PYTHON.same_class_plain_decorators == {"functools.total_ordering", "typing.final", "typing.runtime_checkable"}
    assert len(PYTHON.result_keeping) == 13 and "functools.lru_cache" in PYTHON.result_keeping
    assert PYTHON.partial_names == {"functools.partial"}
    assert PYTHON.wraps_names == {"functools.wraps"}
    assert (PYTHON.constructor_name, PYTHON.allocator_name, PYTHON.implicit_base) == ("__init__", "__new__", "object")
    assert PYTHON.has_universal_member("__repr__") and not PYTHON.has_universal_member("save")
    assert PYTHON.failure_prefixes == ("FAILED ", "ERROR ")


def build_scopes(constructor: str) -> dict:
    """Facts for `class Foo` with two constructors, and a function that calls Foo()."""
    module = facts.Scope("m", "module", "m", "m.src", 1, 9, None)
    module.names["Foo"] = facts.Binding("def", "m.Foo")
    module.names["run"] = facts.Binding("def", "m.run")
    cls = facts.Scope("m.Foo", "class", "m", "m.src", 1, 5, "m")
    cls.names["__init__"] = facts.Binding("def", "m.Foo.__init__")
    cls.names["constructor"] = facts.Binding("def", "m.Foo.constructor")
    init = facts.Scope("m.Foo.__init__", "method", "m", "m.src", 2, 3, "m.Foo")
    other = facts.Scope("m.Foo.constructor", "method", "m", "m.src", 4, 5, "m.Foo")
    run = facts.Scope("m.run", "function", "m", "m.src", 7, 9, "m")
    run.calls.append(facts.RawCall([constructor], constructor, 8))
    return {s.id: s for s in (module, cls, init, other, run)}


def test_the_resolver_runs_the_constructor_its_profile_names():
    scopes = build_scopes("Foo")
    default = Resolver(scopes, None, []).run()
    custom = Resolver(scopes, None, [], replace(PYTHON, constructor_name="constructor")).run()
    assert [(e.callee, e.kind) for e in default.edges] == [("m.Foo.__init__", "constructor")]
    assert [(e.callee, e.kind) for e in custom.edges] == [("m.Foo.constructor", "constructor")]


def test_the_resolver_takes_builtin_names_from_its_profile():
    scopes = build_scopes("print")
    assert Resolver(scopes, None, []).run().external_calls == 1
    quiet = Resolver(scopes, None, [], replace(PYTHON, builtin_names=frozenset())).run()
    assert quiet.external_calls == 0 and [u.text for u in quiet.unresolved] == ["print"]


def test_callers_of_a_class_include_the_callers_of_the_constructor_its_profile_names(make_repo):
    repo = make_repo({"m.py": "class Foo:\n    def __init__(self): pass\n    def build(self): pass\n\ndef run():\n    Foo()\n"})
    graph = build_graph(repo)
    assert [c["symbol"] for c in callers(graph, "Foo")["callers"]] == ["m.run"]
    renamed = replace(PYTHON, constructor_name="build")
    assert callers(graph, "Foo", renamed)["callers"] == []
    assert [c["symbol"] for c in callers(graph, "Foo.__init__", renamed)["callers"]] == ["m.run"]


def test_impact_lists_tests_through_the_profile_collection(make_repo):
    repo = make_repo({"m.py": "def f(): pass\n", "test_m.py": "from m import f\n\ndef test_f():\n    f()\n"})
    graph = build_graph(repo)
    assert [t["node_id"] for t in impact(graph, "f")["tests"]] == ["test_m.py::test_f"]
    silent = replace(PYTHON, test_collection=lambda graph: type("None_", (), {"node_ids": lambda self, sid: []})())
    assert impact(graph, "f", profile=silent)["tests"] == []


def test_the_source_suffixes_of_the_profile_pick_the_files_that_are_indexed_and_fingerprinted(make_repo):
    repo = make_repo({"a.py": "", "b.pyi": "", "c.txt": ""})
    stubs = replace(PYTHON, source_suffixes=(".pyi", ".txt"))
    assert [p.name for p in find_source_files(repo)] == ["a.py"]
    assert [p.name for p in find_source_files(repo, stubs)] == ["b.pyi", "c.txt"]
    assert sorted(fingerprint(repo)) == ["a.py"]
    assert sorted(fingerprint(repo, stubs)) == ["b.pyi", "c.txt"]


def test_the_checker_asks_the_profile_for_the_test_command(tmp_path):
    other = replace(PYTHON, default_test_command=lambda repo: ["npx", "vitest", "run"])
    assert default_test_command(tmp_path, other) == ["npx", "vitest", "run"]
    assert default_test_command(tmp_path)[1:] == ["-m", "pytest", "-q"]


@pytest.mark.parametrize("field", ["name", "builtin_names", "constructor_name"])
def test_a_profile_is_frozen(field):
    with pytest.raises(Exception):
        setattr(PYTHON, field, "x")
