"""Calls on values whose class is known and lives outside the repo, and the reasons
an unresolved call gives for not knowing a receiver's class."""

import importlib

import pytest
from conftest import callers_of

from ripple.indexer import build_graph
from ripple.model import Graph
from ripple.resolver import EXT_INSTANCE


def unresolved(graph):
    return sorted((u.text, u.reason) for u in graph.unresolved)


# -- classes outside the repo ---------------------------------------------


def test_a_stdlib_class_built_in_place_is_outside_the_repo(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                from collections import deque

                class Log:
                    def append(self, line):
                        pass

                def recent():
                    queue = deque()
                    queue.append("x")
                """
        }
    )
    g = build_graph(repo)
    assert g.unresolved == []
    assert callers_of(g, "m.Log.append") == set()
    assert g.external_reasons[EXT_INSTANCE] == 1


def test_a_value_annotated_with_a_stdlib_class_is_outside_the_repo(make_repo):
    repo = make_repo({"m.py": "import threading\n\ndef f(done: threading.Event):\n    done.set()\n"})
    g = build_graph(repo)
    assert g.unresolved == []
    assert g.external_reasons[EXT_INSTANCE] == 1


def test_a_self_attribute_built_from_a_stdlib_class_is_outside_the_repo(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                import threading

                class Gate:
                    def __init__(self):
                        self.ready = threading.Event()

                    def open(self):
                        self.ready.set()
                """
        }
    )
    assert build_graph(repo).unresolved == []


def test_an_attribute_of_an_outside_object_may_hold_a_repo_object(make_repo):
    # threading.local stores whatever the code puts on it, so .chal.get() may be the repo's.
    repo = make_repo(
        {
            "m.py": """\
                import threading

                class Chal:
                    def get(self, key):
                        pass

                class Auth:
                    def __init__(self):
                        self.local = threading.local()

                    def build(self):
                        self.local.chal = Chal()
                        self.local.chal.get("qop")
                        self.local.clear()
                """
        }
    )
    g = build_graph(repo)
    assert unresolved(g) == [
        ("self.local.chal.get", "an attribute of an object from outside the repo may hold anything")
    ]


def test_an_annotated_stdlib_class_that_a_repo_subclass_overrides_stays_unresolved(make_repo):
    # Whatever is passed may be the subclass, so reaching its override cannot be ruled out.
    repo = make_repo(
        {
            "m.py": """\
                from collections import deque

                class Loud(deque):
                    def extend(self, items):
                        pass

                def trim(buf: deque):
                    buf.append("z")
                    buf.extend(["w"])
                """
        }
    )
    g = build_graph(repo)
    assert unresolved(g) == [
        ("buf.extend", "'extend' may be overridden by a repo subclass of collections.deque, which the value could be")
    ]


def test_a_stdlib_class_built_in_place_is_exactly_that_class_so_a_subclass_override_is_not_reached(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                from collections import deque

                class Loud(deque):
                    def extend(self, items):
                        pass

                def recent():
                    queue = deque()
                    queue.extend(["w"])
                """
        }
    )
    g = build_graph(repo)
    assert g.unresolved == []
    assert callers_of(g, "m.Loud.extend") == set()


def test_a_class_a_repo_class_inherits_from_is_provably_a_class(make_repo):
    # `requests` is not installed here and not in the repo, but a name used as a base is a class.
    repo = make_repo(
        {
            "m.py": """\
                import requests

                class Tracing(requests.Session):
                    def request(self, *args):
                        pass

                def fetch(session: requests.Session):
                    session.close()
                    session.request("GET")
                """
        }
    )
    g = build_graph(repo)
    assert unresolved(g) == [
        (
            "session.request",
            "'request' may be overridden by a repo subclass of requests.Session, which the value could be",
        )
    ]


def test_a_name_from_outside_the_repo_not_proven_a_class_stays_unresolved(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                import re
                import requests
                from typing import Any

                def a():
                    pattern = re.compile("x")
                    pattern.match("y")

                def b():
                    session = requests.Session()
                    session.get("u")

                def c(thing: Any):
                    thing.go()
                """
        }
    )
    g = build_graph(repo)
    assert unresolved(g) == [
        (
            "pattern.match",
            "the receiver is the result of a call into code outside the repo, not proven to build a class",
        ),
        ("session.get", "the receiver is the result of a call into code outside the repo, not proven to build a class"),
        ("thing.go", "the receiver is annotated with a name from outside the repo that ripple cannot prove is a class"),
    ]


def test_an_abc_or_protocol_is_not_a_class_a_repo_class_must_inherit_from(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                from collections.abc import Mapping

                class Mine(Mapping):
                    def get(self, key):
                        pass

                def f(m: Mapping):
                    m.get("k")
                """
        }
    )
    g = build_graph(repo)
    assert [u.text for u in g.unresolved] == ["m.get"]


def test_ripple_imports_only_the_stdlib_modules_on_its_list(make_repo, monkeypatch):
    asked = []
    real = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", lambda name, *a: asked.append(name) or real(name, *a))
    repo = make_repo(
        {
            "m.py": """\
                import antigravity
                from pathlib import Path

                def f():
                    browser = antigravity.Browser()
                    browser.open()
                    here = Path(".")
                    here.exists()
                """
        }
    )
    g = build_graph(repo)
    assert "antigravity" not in asked and "pathlib" in asked
    assert [u.text for u in g.unresolved] == ["browser.open"]


def test_external_reasons_survive_the_cache_round_trip(make_repo):
    repo = make_repo({"m.py": "import threading\n\ndef f(e: threading.Event):\n    e.set()\n    len([])\n"})
    g = build_graph(repo)
    assert g.external_reasons
    assert Graph.from_dict(g.to_dict()).external_reasons == g.external_reasons
    assert g.stats()["external_reasons"] == g.external_reasons


# -- why a receiver's class is unknown ------------------------------------

REASON_CASES = {
    "the receiver is an unannotated parameter": "def f(x):\n    x.go()\n",
    "the receiver is annotated with something that does not name one class": "def f(x: list[int] | dict):\n    x.go()\n",
    "the receiver is *args or **kwargs": "def f(*args):\n    args.go()\n",
    "the receiver is a loop variable": "def f(xs):\n    for x in xs:\n        x.go()\n",
    "the receiver is bound by a with statement": "def f(p):\n    with p as x:\n        x.go()\n",
    "the receiver is a caught exception": "def f():\n    try:\n        pass\n    except ValueError as x:\n        x.go()\n",
    "the receiver is a comprehension variable": "def f(xs):\n    return [x.go() for x in xs]\n",
    "the receiver is unpacked from a tuple, list or star target": "def f(p):\n    a, b = p\n    a.go()\n",
    "the receiver is a local assigned from an expression ripple cannot type": "def f(p):\n    x = p.thing\n    x.go()\n",
    "the receiver is assigned the result of a call whose callee is computed": "def f(p):\n    x = p()()\n    x.go()\n",
    "the receiver is a lambda parameter": "f = lambda x: x.go()\n",
    "the receiver is copied from a variable whose class is not known": "def f(p):\n    x = p\n    x.go()\n",
    "the receiver is the result of a call to a function with no return annotation": (
        "def make():\n    pass\n\ndef f():\n    x = make()\n    x.go()\n"
    ),
    "the receiver is the result of a call whose return annotation does not name a class ripple can follow": (
        "def make() -> list[int] | dict:\n    pass\n\ndef f():\n    x = make()\n    x.go()\n"
    ),
    "the receiver is the result of a call ripple cannot resolve": "def f():\n    x = nowhere()\n    x.go()\n",
    "the receiver is assigned values of different classes": (
        "class A:\n    def go(self):\n        pass\n\nclass B:\n    def go(self):\n        pass\n\n"
        "def f(flag):\n    x = A()\n    if flag:\n        x = B()\n    x.go()\n"
    ),
}


@pytest.mark.parametrize("reason", sorted(REASON_CASES))
def test_each_way_a_receiver_stays_unknown_has_its_own_reason(make_repo, reason):
    g = build_graph(make_repo({"m.py": REASON_CASES[reason]}))
    assert reason in {u.reason for u in g.unresolved if u.text.endswith(".go")}, unresolved(g)
