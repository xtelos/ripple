"""Real repositories contain files ripple was not written for: broken symlinks,
unreadable files, odd encodings, generated code nested a thousand levels deep.
One such file must cost that file, never the whole index."""

import os
import subprocess
import sys
import threading

import pytest
from conftest import callees_of, callers_of

from ripple import indexer
from ripple.cache import fingerprint, load_graph
from ripple.indexer import build_graph

# Before 3.12 the recursion limit is not raised (see indexer.RAISE_LIMIT), so code
# nested this deep costs its file there instead of being indexed.
needs_raised_limit = pytest.mark.skipif(not indexer.RAISE_LIMIT, reason="the recursion limit is not raised before 3.12")

GOOD = "def target():\n    return 1\n\ndef user():\n    return target()\n"


def test_broken_symlink_does_not_crash_the_index(make_repo):
    repo = make_repo({"good.py": GOOD})
    os.symlink(repo / "missing.py", repo / "broken.py")
    os.symlink(repo / "self.py", repo / "self.py")  # a loop: resolving it raises ELOOP
    assert "broken.py" not in fingerprint(repo)
    graph, _ = load_graph(repo)
    assert callers_of(graph, "good.target") == {"good.user"}
    assert graph.parse_errors == []


def test_symlinked_directory_loop_is_not_followed(make_repo):
    repo = make_repo({"pkg/good.py": GOOD})
    os.symlink(repo, repo / "pkg" / "loop", target_is_directory=True)
    graph, _ = load_graph(repo)
    assert set(graph.modules) == {"pkg/good.py"}


def test_symlink_to_a_file_is_still_indexed(make_repo):
    repo = make_repo({"good.py": GOOD})
    os.symlink(repo / "good.py", repo / "alias.py")
    graph, _ = load_graph(repo)
    assert "alias.py" in graph.modules


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_named_pipe_called_x_py_is_skipped_not_read(make_repo):
    repo = make_repo({"good.py": GOOD})
    os.mkfifo(repo / "pipe.py")  # opening it for reading would block forever
    done = subprocess.run(
        [sys.executable, "-m", "ripple", "index", str(repo)], capture_output=True, text=True, timeout=15
    )
    assert done.returncode == 0, done.stderr
    assert "1 files" in done.stdout


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="root reads anything")
def test_unreadable_file_is_reported_not_fatal(make_repo):
    repo = make_repo({"good.py": GOOD, "secret.py": "def f():\n    pass\n"})
    os.chmod(repo / "secret.py", 0)
    try:
        graph = build_graph(repo)
    finally:
        os.chmod(repo / "secret.py", 0o644)
    assert graph.parse_errors == ["secret.py"]
    assert callers_of(graph, "good.target") == {"good.user"}


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="root enters anything")
def test_a_directory_that_can_be_listed_but_not_entered_is_skipped_and_reported(make_repo):
    repo = make_repo({"good.py": GOOD, "locked/inside.py": "def f():\n    pass\n"})
    os.chmod(repo / "locked", 0o644)  # readable, not searchable: stat of anything inside fails
    try:
        graph = build_graph(repo)
    finally:
        os.chmod(repo / "locked", 0o755)
    assert graph.skipped_dirs == ["locked"]
    assert callers_of(graph, "good.target") == {"good.user"}


def test_a_file_whose_type_cannot_be_determined_is_left_to_the_read(make_repo, monkeypatch):
    repo = make_repo({"good.py": GOOD, "odd.py": "def f():\n    pass\n"})
    real = type(repo).is_file

    def is_file(self):
        if self.name == "odd.py":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(type(repo), "is_file", is_file)
    graph = build_graph(repo)
    # not skipped silently: reading it decides (here it reads fine; an unreadable one is a parse error)
    assert "odd.py" in graph.modules
    assert callers_of(graph, "good.target") == {"good.user"}


def test_utf8_bom_is_not_a_parse_error(make_repo):
    repo = make_repo({"good.py": GOOD})
    (repo / "bom.py").write_bytes(b"\xef\xbb\xbffrom good import target\n\ndef f():\n    return target()\n")
    graph = build_graph(repo)
    assert graph.parse_errors == []
    assert callers_of(graph, "good.target") == {"good.user", "bom.f"}


def test_coding_cookie_is_honoured(make_repo):
    repo = make_repo({"good.py": GOOD})
    (repo / "old.py").write_bytes(
        b"# -*- coding: latin-1 -*-\nfrom good import target\n\ndef f():\n    s = '\xe9'\n    return target()\n"
    )
    graph = build_graph(repo)
    assert graph.parse_errors == []
    assert callers_of(graph, "good.target") == {"good.user", "old.f"}


def test_bytes_that_are_not_python_cost_only_their_file(make_repo):
    repo = make_repo({"good.py": GOOD})
    (repo / "badutf8.py").write_bytes(b"def f():\n    return '\xff\xfe'\n")
    (repo / "nul.py").write_bytes(b"def f():\n    return 1\n\x00\n")
    graph = build_graph(repo)
    assert sorted(graph.parse_errors) == ["badutf8.py", "nul.py"]
    assert callers_of(graph, "good.target") == {"good.user"}


# Generated code nests far deeper than the interpreter's default recursion limit
# of 1000 allows a recursive visitor to follow: one branch per enum member, a
# table built with "+", a long builder chain.


MODERN_SYNTAX = {
    "match_stmt.py": (
        (3, 10),
        "from good import target\n\ndef f(x):\n    match x:\n        case [a, *rest]:\n            return target()\n"
        "        case {'k': v, **kw}:\n            return v\n        case Point(x=1) as p:\n            return p\n",
    ),
    "walrus.py": (
        (3, 8),
        "from good import target\n\ndef f(xs):\n    if (n := len(xs)) > 1:\n        return target()\n"
        "    return [y for x in xs if (y := x)]\n",
    ),
    "except_star.py": (
        (3, 11),
        "from good import target\n\ndef f():\n    try:\n        pass\n    except* ValueError as e:\n        target()\n",
    ),
    "pep695.py": (
        (3, 12),
        "from good import target\n\nclass Box[T]:\n    def get(self) -> T:\n        return target()\n\n"
        "def ident[T](x: T) -> T:\n    return target()\n\ntype Alias[K] = dict[K, int]\n",
    ),
    "fstring.py": (
        (3, 8),
        "from good import target\n\ndef f(x):\n    return f\"{target()!r:>{10}} {f'{x}'}\"\n",
    ),
}


@pytest.mark.parametrize("name", MODERN_SYNTAX)
def test_modern_syntax_is_indexed(make_repo, name):
    minimum, source = MODERN_SYNTAX[name]
    if sys.version_info < minimum:
        pytest.skip(f"needs Python {minimum[0]}.{minimum[1]}")
    repo = make_repo({"good.py": GOOD, name: source})
    graph = build_graph(repo)
    assert graph.parse_errors == []
    assert any(c.startswith(name.removesuffix(".py") + ".") for c in callers_of(graph, "good.target"))


@needs_raised_limit
def test_long_elif_chain_is_indexed(make_repo):
    branches = "".join(f"    elif x == {i}:\n        return target()\n" for i in range(1, 1500))
    repo = make_repo({"good.py": GOOD, "gen.py": "from good import target\n\ndef f(x):\n    if x == 0:\n        return 0\n" + branches})
    graph = build_graph(repo)
    assert graph.parse_errors == []
    assert callers_of(graph, "good.target") == {"good.user", "gen.f"}


@needs_raised_limit
def test_long_binop_chain_is_indexed(make_repo):
    repo = make_repo({"good.py": GOOD, "gen.py": "from good import target\n\ndef f():\n    return " + " + ".join(["target()"] * 3000) + "\n"})
    graph = build_graph(repo)
    assert graph.parse_errors == []
    assert callers_of(graph, "good.target") == {"good.user", "gen.f"}


@needs_raised_limit
def test_long_attribute_chain_is_indexed(make_repo):
    repo = make_repo({"good.py": GOOD, "gen.py": "import good\n\ndef f(a):\n    a" + ".b" * 3000 + "()\n    return good.target()\n"})
    graph = build_graph(repo)
    assert graph.parse_errors == []
    assert callers_of(graph, "good.target") == {"good.user", "gen.f"}


def test_long_chain_of_copied_variables_does_not_crash_the_resolver(make_repo):
    copies = "".join(f"v{i} = v{i - 1}\n" for i in range(1, 3000))
    repo = make_repo({"good.py": GOOD, "gen.py": "v0 = 1\n" + copies + "\ndef f():\n    return v2999.bit_length()\n"})
    graph = build_graph(repo)
    assert callers_of(graph, "good.target") == {"good.user"}


def test_nesting_beyond_any_limit_costs_only_that_file_or_call(make_repo):
    repo = make_repo({"good.py": GOOD, "huge.py": "x = " + " + ".join(["1"] * 400_000) + "\n"})
    graph = build_graph(repo)
    assert callers_of(graph, "good.target") == {"good.user"}


def test_index_leaves_the_recursion_limit_alone(make_repo):
    repo = make_repo({"good.py": GOOD})
    before = sys.getrecursionlimit()
    build_graph(repo)
    assert sys.getrecursionlimit() == before


def test_the_recursion_limit_is_raised_only_by_the_big_stack_thread(monkeypatch):
    # The limit is process-wide and only the worker has a big stack, so on an
    # interpreter where deep Python recursion uses the C stack (3.10), raising it from
    # the caller would let another thread's runaway recursion overflow its small stack.
    callers = []
    real = sys.setrecursionlimit
    monkeypatch.setattr(sys, "setrecursionlimit", lambda n: (callers.append(threading.get_ident()), real(n))[1])
    monkeypatch.setattr(indexer, "RAISE_LIMIT", True)
    inside = indexer.run_deep(sys.getrecursionlimit)
    assert inside >= indexer.DEEP_RECURSION_LIMIT
    assert callers and threading.get_ident() not in callers


def test_where_raising_the_limit_is_unsafe_the_worker_runs_on_the_default(monkeypatch):
    monkeypatch.setattr(indexer, "RAISE_LIMIT", False)
    before = sys.getrecursionlimit()
    assert indexer.run_deep(sys.getrecursionlimit) == before
    assert sys.getrecursionlimit() == before


def test_package_that_imports_its_own_submodule_resolves_it(make_repo):
    # alembic/__init__.py says `from . import op`, and callers say `from alembic import op`.
    repo = make_repo(
        {
            "pkg/__init__.py": "from . import util\n",
            "pkg/util.py": "def helper():\n    pass\n",
            "app.py": "from pkg import util\nfrom pkg import util as u\n\ndef f():\n    util.helper()\n\ndef g():\n    u.helper()\n",
        }
    )
    graph = build_graph(repo)
    assert callers_of(graph, "pkg.util.helper") == {"app.f", "app.g"}
    assert not [u for u in graph.unresolved if "cycle" in u.reason]


def test_a_star_import_that_already_bound_the_name_beats_the_submodule(make_repo):
    # `from . import util` returns the attribute pkg.util when one is already set, and
    # imports the submodule only when it is missing: here helpers' function wins.
    repo = make_repo(
        {
            "pkg/__init__.py": "from .helpers import *\nfrom . import util\n",
            "pkg/helpers.py": "def util():\n    pass\n",
            "pkg/util.py": "def helper():\n    pass\n",
            "app.py": "from pkg import util\n\ndef f():\n    util()\n\ndef g():\n    util.helper()\n",
        }
    )
    graph = build_graph(repo)
    assert callers_of(graph, "pkg.helpers.util") == {"app.f"}
    assert callers_of(graph, "pkg.util.helper") == set()


def test_a_star_import_that_does_not_define_the_name_leaves_the_submodule(make_repo):
    repo = make_repo(
        {
            "pkg/__init__.py": "from .helpers import *\nfrom . import util\n",
            "pkg/helpers.py": "def other():\n    pass\n",
            "pkg/util.py": "def helper():\n    pass\n",
            "app.py": "from pkg import util\n\ndef g():\n    util.helper()\n",
        }
    )
    assert callers_of(build_graph(repo), "pkg.util.helper") == {"app.g"}


def test_package_importing_a_name_that_is_not_a_submodule_is_not_called_a_cycle(make_repo):
    repo = make_repo(
        {"pkg/__init__.py": "from . import nothing_here\n", "app.py": "from pkg import nothing_here\n\ndef f():\n    nothing_here.go()\n"}
    )
    graph = build_graph(repo)
    assert [u.reason for u in graph.unresolved] == ["'nothing_here' is not defined in module pkg"]


# A hub class (Django's Model, a framework base) has hundreds of subclasses, and
# every call through it asks for all of them.


class _CountingDict(dict):
    gets = 0

    def get(self, key, default=None):
        _CountingDict.gets += 1
        return super().get(key, default)


def test_subclass_closure_is_computed_once_per_class_not_once_per_call(make_repo, monkeypatch):
    from ripple.resolver import Resolver

    original = Resolver._direct_subclasses
    monkeypatch.setattr(Resolver, "_direct_subclasses", lambda self: _CountingDict(original(self)))
    subclasses = "".join(f"class Sub{i}(Base):\n    def hook(self):\n        pass\n" for i in range(200))
    calls = "".join("    b.hook()\n" for _ in range(400))
    source = "class Base:\n    def hook(self):\n        pass\n\n" + subclasses + "\ndef run(b: Base):\n" + calls
    repo = make_repo({"hub.py": source})
    _CountingDict.gets = 0
    graph = build_graph(repo)
    # one edge to Base.hook and one override edge per subclass, for each call
    assert len([e for e in graph.edges if e.caller == "hub.run"]) == 400 * 201
    assert _CountingDict.gets < 5_000  # 400 calls x 200 subclasses = 80,000 without the memo


@needs_raised_limit
def test_very_deep_class_chain_indexes_quickly(make_repo):
    import time

    chain = "".join(f"class C{i}(C{i - 1}):\n    pass\n" for i in range(1, 2000))
    source = "class C0:\n    def m(self):\n        pass\n\n" + chain + "\ndef f(c: C1999):\n    c.m()\n"
    repo = make_repo({"deep.py": source})
    start = time.perf_counter()
    graph = build_graph(repo)
    assert callers_of(graph, "deep.C0.m") == {"deep.f"}
    assert time.perf_counter() - start < 3  # about 9 s while the method resolution order was rebuilt with list scans


def test_super_in_a_mixin_is_unresolved_not_outside_the_repo(make_repo):
    # A mixin's super().render() reaches whichever class comes next in the MRO of the
    # concrete class (Page here): not provably code outside the repo, unlike super().__init__().
    repo = make_repo(
        {
            "views.py": """\
                class Base:
                    def render(self):
                        pass

                class Mixin:
                    def __init__(self):
                        super().__init__()

                    def render(self):
                        return super().render()

                class Page(Mixin, Base):
                    pass
                """
        }
    )
    graph = build_graph(repo)
    assert [(u.caller, u.text) for u in graph.unresolved] == [("views.Mixin.render", "super().render")]
    # and super().__init__() stays outside the repo: object defines __init__


def test_overrides_are_not_linked_when_the_exact_class_is_known(make_repo):
    # `field = Field()` makes field exactly a Field, so Sub.run cannot run; a
    # parameter annotated Field, self, or a self.field a subclass of Holder
    # may reassign could be a subclass.
    repo = make_repo(
        {
            "fields.py": """\
                class Field:
                    def run(self):
                        pass

                class Sub(Field):
                    def run(self):
                        pass

                def built():
                    field = Field()
                    field.run()

                class Holder:
                    def __init__(self):
                        self.field = Field()

                    def go(self):
                        self.field.run()

                def annotated(field: Field):
                    field.run()
                """
        }
    )
    graph = build_graph(repo)
    assert callees_of(graph, "fields.built") == {"fields.Field", "fields.Field.run"}  # Field has no __init__
    assert callees_of(graph, "fields.Holder.go") == {"fields.Field.run", "fields.Sub.run"}
    assert callees_of(graph, "fields.annotated") == {"fields.Field.run", "fields.Sub.run"}


def test_a_class_whose_constructor_can_return_a_subclass_is_not_exact(make_repo):
    # anyio's Event.__new__ returns a backend subclass: `Event().wait()` runs an
    # override. __new__ (own or inherited), a metaclass (own or inherited) each
    # break the proof that `x = C()` is exactly a C.
    repo = make_repo(
        {
            "shapes.py": """\
                class Event:
                    def __new__(cls):
                        return object.__new__(RealEvent)

                    def wait(self):
                        raise NotImplementedError

                class Derived(Event):
                    pass

                class RealEvent(Derived):
                    def wait(self):
                        pass

                class Meta(type):
                    def __call__(cls):
                        return object.__new__(Fast)

                class Slow(metaclass=Meta):
                    def go(self):
                        pass

                class Child(Slow):
                    pass

                class Fast(Child):
                    def go(self):
                        pass

                def own_new():
                    event = Event()
                    event.wait()

                def inherited_new():
                    event = Derived()
                    event.wait()

                def own_metaclass():
                    thing = Slow()
                    thing.go()

                def inherited_metaclass():
                    thing = Child()
                    thing.go()
                """
        }
    )
    graph = build_graph(repo)
    assert "shapes.RealEvent.wait" in callees_of(graph, "shapes.own_new")
    assert "shapes.RealEvent.wait" in callees_of(graph, "shapes.inherited_new")
    assert "shapes.Fast.go" in callees_of(graph, "shapes.own_metaclass")
    assert "shapes.Fast.go" in callees_of(graph, "shapes.inherited_metaclass")


def test_a_class_with_an_unresolvable_base_is_not_exact(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                def factory():
                    return object

                class Odd(factory()):
                    def go(self):
                        pass

                class Sub(Odd):
                    def go(self):
                        pass

                def use():
                    odd = Odd()
                    odd.go()
                """
        }
    )
    assert "m.Sub.go" in callees_of(build_graph(repo), "m.use")


def test_a_class_reached_through_self_is_not_exact(make_repo):
    # self may be a subclass that redefines the nested class.
    repo = make_repo(
        {
            "m.py": """\
                class Base:
                    class Widget:
                        def run(self):
                            pass

                    def make(self):
                        w = self.Widget()
                        w.run()

                    @classmethod
                    def build(cls):
                        w = cls.Widget()
                        w.run()

                class Sub(Base):
                    class Widget(Base.Widget):
                        def run(self):
                            pass

                def static():
                    w = Base.Widget()
                    w.run()
                """
        }
    )
    graph = build_graph(repo)
    assert "m.Sub.Widget.run" in callees_of(graph, "m.Base.make")
    assert "m.Sub.Widget.run" in callees_of(graph, "m.Base.build")
    assert callees_of(graph, "m.static") == {"m.Base.Widget", "m.Base.Widget.run"}


def test_a_base_that_is_an_instance_attribute_does_not_crash_the_index(make_repo):
    # jsonschema's tests say `class OhNo(self.Validator):` inside a method. Resolving
    # that base asks about subclasses before the hierarchy exists.
    repo = make_repo(
        {
            "m.py": """\
                class Base:
                    def run(self):
                        pass

                    def make(self):
                        class OhNo(self.Missing):
                            pass

                        odd = OhNo()
                        odd.anything()

                class Sub(Base):
                    def run(self):
                        pass

                def use(b: Base):
                    b.run()
                """
        }
    )
    graph = build_graph(repo)
    assert callees_of(graph, "m.use") == {"m.Base.run", "m.Sub.run"}
    reasons = {u.text: u.reason for u in graph.unresolved}
    assert "base class self.Missing of m.Base.make.OhNo could not be resolved" in reasons["odd.anything"]
