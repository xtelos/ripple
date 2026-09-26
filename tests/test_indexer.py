from conftest import callees_of, callers_of

from ripple.indexer import build_graph


def test_symbols_have_kind_file_and_line(make_repo):
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": """\
                def top():
                    pass

                class Box:
                    def open(self):
                        def inner():
                            pass
                """,
        }
    )
    g = build_graph(repo)
    assert g.symbols["pkg"].kind == "module"
    assert g.symbols["pkg.mod"].kind == "module"
    assert g.symbols["pkg.mod.top"].kind == "function"
    assert (g.symbols["pkg.mod.top"].file, g.symbols["pkg.mod.top"].line) == ("pkg/mod.py", 1)
    assert g.symbols["pkg.mod.Box"].kind == "class"
    assert g.symbols["pkg.mod.Box.open"].kind == "method"
    assert g.symbols["pkg.mod.Box.open.inner"].kind == "function"


def test_same_module_call_and_module_level_caller(make_repo):
    repo = make_repo({"app.py": "def a():\n    b()\n\ndef b():\n    pass\n\na()\n"})
    g = build_graph(repo)
    assert callers_of(g, "app.b") == {"app.a"}
    assert callers_of(g, "app.a") == {"app"}


def test_import_forms(make_repo):
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/util.py": "def helper():\n    pass\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/deep.py": "def far():\n    pass\n",
            "use_from.py": "from pkg.util import helper\n\ndef f():\n    helper()\n",
            "use_alias.py": "from pkg.util import helper as h\n\ndef f():\n    h()\n",
            "use_module.py": "import pkg.util\n\ndef f():\n    pkg.util.helper()\n",
            "use_module_alias.py": "import pkg.sub.deep as d\n\ndef f():\n    d.far()\n",
            "use_submodule.py": "from pkg import util\n\ndef f():\n    util.helper()\n",
            "pkg/rel.py": "from .util import helper\nfrom .sub import deep\n\ndef f():\n    helper()\n    deep.far()\n",
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "pkg.util.helper") == {
        "use_from.f",
        "use_alias.f",
        "use_module.f",
        "use_submodule.f",
        "pkg.rel.f",
    }
    assert callers_of(g, "pkg.sub.deep.far") == {"use_module_alias.f", "pkg.rel.f"}


def test_reexport_through_package_init(make_repo):
    repo = make_repo(
        {
            "pkg/__init__.py": "from .impl import run\n",
            "pkg/impl.py": "def run():\n    pass\n",
            "main.py": "from pkg import run\n\ndef go():\n    run()\n",
        }
    )
    assert callers_of(build_graph(repo), "pkg.impl.run") == {"main.go"}


def test_self_inherited_and_super_calls(make_repo):
    repo = make_repo(
        {
            "shapes.py": """\
                class Base:
                    def __init__(self, name):
                        self.name = name

                    def describe(self):
                        return self.label()

                    def label(self):
                        return self.name

                class Square(Base):
                    def __init__(self):
                        super().__init__("square")

                    def area(self):
                        return self.describe()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "shapes.Base.label") == {"shapes.Base.describe"}
    assert callers_of(g, "shapes.Base.describe") == {"shapes.Square.area"}
    assert callers_of(g, "shapes.Base.__init__") == {"shapes.Square.__init__"}


def test_constructor_targets_init_or_class(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class WithInit:
                    def __init__(self):
                        pass

                class Child(WithInit):
                    pass

                class Plain:
                    pass

                def make():
                    WithInit()
                    Child()
                    Plain()
                """
        }
    )
    g = build_graph(repo)
    assert callees_of(g, "m.make") == {"m.WithInit.__init__", "m.Plain"}
    assert {e.kind for e in g.edges if e.caller == "m.make"} == {"constructor"}


def test_override_edges_from_base_class_self_call(make_repo):
    repo = make_repo(
        {
            "d.py": """\
                class Base:
                    def run(self):
                        return self.step()

                    def step(self):
                        raise NotImplementedError

                class Fast(Base):
                    def step(self):
                        return 1
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "d.Fast.step") == {"d.Base.run"}
    [edge] = [e for e in g.edges if e.callee == "d.Fast.step"]
    assert edge.kind == "override"


def test_typed_receivers(make_repo):
    repo = make_repo(
        {
            "store.py": "class Store:\n    def get(self):\n        pass\n\n    def put(self):\n        pass\n",
            "svc.py": """\
                from typing import Optional
                from store import Store

                GLOBAL = Store()

                class Service:
                    def __init__(self, store: Optional[Store] = None):
                        self.store = store or Store()

                    def read(self):
                        return self.store.get()

                def with_param(s: "Store"):
                    s.put()

                def with_local():
                    s = Store()
                    s.get()

                def with_global():
                    GLOBAL.put()
                """,
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "store.Store.get") == {"svc.Service.read", "svc.with_local"}
    assert callers_of(g, "store.Store.put") == {"svc.with_param", "svc.with_global"}


def test_conflicting_local_types_are_not_guessed(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class A:
                    def go(self):
                        pass

                class B:
                    def go(self):
                        pass

                def f(flag):
                    x = A()
                    if flag:
                        x = B()
                    x.go()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.A.go") == set()
    assert callers_of(g, "m.B.go") == set()
    assert any(u.caller == "m.f" and u.text == "x.go" for u in g.unresolved)


def test_self_referential_assignments_terminate(make_repo):
    # Found by running ripple on itself: `line = line.rstrip()` made type
    # inference chase its own tail.
    repo = make_repo(
        {
            "m.py": """\
                class Node:
                    def __init__(self):
                        self.next = self.next.follow()

                def f(line, a, b):
                    line = line.rstrip()
                    a = b.x()
                    b = a.y()
                    return a.z()
                """
        }
    )
    g = build_graph(repo)
    assert "m.f" in g.symbols


def test_global_and_nonlocal_bind_in_the_right_scope(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class A:
                    def go(self):
                        pass

                class B:
                    def go(self):
                        pass

                CURRENT = A()

                def swap():
                    global CURRENT
                    CURRENT = B()

                def use():
                    CURRENT.go()

                def outer():
                    helper = A()

                    def inner():
                        nonlocal helper
                        helper.go()

                    return inner
                """
        }
    )
    g = build_graph(repo)
    # CURRENT may be an A or a B, so ripple must not claim either.
    assert "m.use" not in callers_of(g, "m.A.go")
    # nonlocal refers to outer's helper, which is an A.
    assert callers_of(g, "m.A.go") == {"m.outer.inner"}


def test_match_captures_are_local_names(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class A:
                    def go(self):
                        pass

                item = A()

                def f(value):
                    match value:
                        case [item, *rest]:
                            item.go()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.A.go") == set()


def test_dynamic_calls_are_recorded_unresolved_not_guessed(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                def handler():
                    pass

                TABLE = {"h": handler}

                def by_table(key):
                    TABLE[key]()

                def by_getattr(obj):
                    getattr(obj, "handler")()

                def by_param(obj):
                    obj.handler()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.handler") == set()
    reasons = {u.caller: u.reason for u in g.unresolved}
    assert set(reasons) == {"m.by_table", "m.by_getattr", "m.by_param"}
    assert g.stats()["unresolved_calls"] == 3


def test_builtins_and_third_party_are_external_not_unresolved(make_repo):
    repo = make_repo({"m.py": "import json\n\ndef f(x):\n    print(len(x))\n    json.dumps(x)\n"})
    g = build_graph(repo)
    assert g.unresolved == []
    assert g.stats()["external_calls"] == 3


def test_methods_on_builtin_values_are_external(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                from typing import Callable, Dict

                class Box:
                    def __init__(self):
                        self.items = {}

                    def get(self, key):
                        return self.items.get(key)

                def f(names: list, lookup: Dict[str, int], text: str, callback: Callable):
                    found = []
                    found.append(1)
                    names.sort()
                    lookup.get("x")
                    text.strip()
                    f"{text}".upper()
                    callback()
                """
        }
    )
    g = build_graph(repo)
    assert [u.text for u in g.unresolved] == ["callback"]
    assert callers_of(g, "m.Box.get") == set()


def test_return_annotations_and_properties_type_values(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class Store:
                    def get(self):
                        pass

                def make_store() -> Store:
                    return Store()

                class Service:
                    def __init__(self):
                        self._store = make_store()
                        self.cache = self._new_cache()

                    def _new_cache(self) -> dict:
                        return {}

                    @property
                    def store(self) -> "Store":
                        return self._store

                    @property
                    def untyped(self):
                        return self._store

                    @untyped.setter
                    def untyped(self, value):
                        self._store = value

                    def read(self):
                        self.cache.get("k")
                        self.store.get()
                        self.untyped.get()

                def f():
                    s = make_store()
                    s.get()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.Store.get") == {"m.Service.read", "m.f"}
    assert [u.text for u in g.unresolved] == ["self.untyped.get"]


def test_members_of_an_external_base_class_are_external(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                import ast

                class Walker(ast.NodeVisitor):
                    def run(self, tree):
                        self.visit(tree)

                class Plain:
                    def run(self):
                        self.missing()
                """
        }
    )
    g = build_graph(repo)
    assert [u.text for u in g.unresolved] == ["self.missing"]


def test_none_assignments_do_not_hide_a_type(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class Conn:
                    def send(self):
                        pass

                class Client:
                    def __init__(self):
                        self.conn = None

                    def connect(self):
                        self.conn = Conn()

                    def ping(self):
                        self.conn.send()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.Conn.send") == {"m.Client.ping"}


def test_decorators_are_called_by_the_defining_scope(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                def deco(fn):
                    return fn

                @deco
                def target():
                    pass
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.deco") == {"m"}
    [edge] = [e for e in g.edges if e.callee == "m.deco"]
    assert edge.kind == "decorator"


def test_syntax_errors_are_skipped_and_reported(make_repo):
    repo = make_repo({"good.py": "def ok():\n    pass\n", "bad.py": "def broken(:\n"})
    g = build_graph(repo)
    assert "good.ok" in g.symbols
    assert g.parse_errors == ["bad.py"]


def test_ignored_directories_are_not_indexed(make_repo):
    repo = make_repo(
        {
            "app.py": "def f():\n    pass\n",
            ".venv/lib/x.py": "def g():\n    pass\n",
            "node_modules/y.py": "def h():\n    pass\n",
        }
    )
    assert {s for s in build_graph(repo).symbols} == {"app", "app.f"}


def test_src_layout_modules_drop_the_src_prefix(make_repo):
    repo = make_repo(
        {
            "src/pkg/__init__.py": "",
            "src/pkg/core.py": "def work():\n    pass\n",
            "tests/test_core.py": "from pkg.core import work\n\ndef test_work():\n    work()\n",
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "pkg.core.work") == {"tests.test_core.test_work"}
    assert g.symbols["pkg.core.work"].file == "src/pkg/core.py"
