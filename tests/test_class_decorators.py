"""`Foo()` is exactly a Foo only if nothing can make the name Foo mean another class. A class decorator
returns what the name is bound to afterwards, so the class counts as exact only when every decorator on
it (and on its bases) is known to hand back the same class: a short list of well known ones from outside
the repo. A decorator that is a repo function or class, or that ripple cannot resolve, may return a
subclass or a wrapper, so the value is not taken to be exactly Foo and its subclass overrides stay linked."""

import textwrap

import pytest
from conftest import callees_of

from ripple.indexer import build_graph

BODY = """\
    class Foo:
        def run(self):
            pass

    class Child(Foo):
        def run(self):
            pass

    def go():
        f = Foo()
        f.run()

    def go_directly():
        Foo().run()
    """

OVERRIDDEN = {"m.Foo.run", "m.Child.run"}
EXACT = {"m.Foo.run"}


def graph_of(make_repo, head: str, decorators: str = "", body: str = BODY):
    """The graph of a module that starts with head and decorates Foo (the first class in body) with decorators."""
    source = textwrap.dedent(head) + "\n" + textwrap.dedent(body)
    if decorators:
        source = source.replace("class Foo:", decorators.strip("\n") + "\nclass Foo:", 1)
    return build_graph(make_repo({"m.py": source}))


def calls_made(graph) -> set:
    return {callee for caller in ("m.go", "m.go_directly") for callee in callees_of(graph, caller)} - {"m.Foo"}


# -- a decorator that can replace the class keeps the override edges -----------

REPLACING = {
    "a repo function that returns a subclass": (
        "def deco(cls):\n    class Sub(cls):\n        def run(self):\n            pass\n    return Sub\n",
        "@deco",
    ),
    "a repo function that returns its argument": ("def deco(cls):\n    return cls\n", "@deco"),
    "a repo decorator factory": ("def make(n):\n    def deco(cls):\n        return cls\n    return deco\n", "@make(3)"),
    "a repo class used as a decorator": ("class deco:\n    def __init__(self, cls):\n        pass\n", "@deco"),
    "a decorator that cannot be resolved": ("from nowhere import deco\n", "@deco"),
    "a decorator that is not a name": ("DECOS = [lambda c: c]\n", "@DECOS[0]"),
    "a call on a name that cannot be resolved": ("from nowhere import factory\n", "@factory(1)"),
    "an outside decorator that is not on the list": ("import six\n", "@six.add_metaclass(type)"),
    "a name from outside that only looks like a listed one": ("from elsewhere import dataclass\n", "@dataclass"),
    "a repo function called dataclass": ("def dataclass(cls):\n    return cls\n", "@dataclass"),
    "total_ordering called as a factory": ("import functools\n", "@functools.total_ordering()"),
    "a listed decorator under one that is not": (
        "from dataclasses import dataclass\nfrom nowhere import deco\n",
        "@deco\n@dataclass",
    ),
    "a listed decorator over one that is not": (
        "from dataclasses import dataclass\nfrom nowhere import deco\n",
        "@dataclass\n@deco",
    ),
}


@pytest.mark.parametrize("how", sorted(REPLACING))
def test_a_class_with_a_decorator_that_may_replace_it_is_not_exact(make_repo, how):
    head, decorators = REPLACING[how]
    graph = graph_of(make_repo, head, decorators)
    assert calls_made(graph) == OVERRIDDEN, how
    assert callees_of(graph, "m.go") >= OVERRIDDEN, how
    assert callees_of(graph, "m.go_directly") >= OVERRIDDEN, how


def test_a_class_with_no_decorator_is_still_exact(make_repo):
    assert calls_made(graph_of(make_repo, "")) == EXACT


def test_a_subclass_of_a_class_that_may_be_replaced_is_not_exact_either(make_repo):
    # class Foo(Base) subclasses whatever the name Base is bound to after its decorator ran
    body = """\
        class Base:
            def run(self):
                pass

        class Foo(Base):
            def run(self):
                pass

        class Child(Foo):
            def run(self):
                pass

        def go():
            f = Foo()
            f.run()

        def go_directly():
            Foo().run()
        """
    head = "from nowhere import deco\n"
    source = textwrap.dedent(head) + "\n" + textwrap.dedent(body).replace("class Base:", "@deco\nclass Base:")
    graph = build_graph(make_repo({"m.py": source}))
    assert calls_made(graph) == {"m.Foo.run", "m.Child.run"}


# -- the decorators that hand the same class back keep it exact -------------

SAME_CLASS = {
    "dataclass": ("from dataclasses import dataclass\n", "@dataclass"),
    "dataclass()": ("from dataclasses import dataclass\n", "@dataclass()"),
    "dataclass(frozen=True)": ("from dataclasses import dataclass\n", "@dataclass(frozen=True, order=True)"),
    "dataclasses.dataclass": ("import dataclasses\n", "@dataclasses.dataclass"),
    "dataclasses.dataclass(...)": ("import dataclasses\n", "@dataclasses.dataclass(slots=True)"),
    "dataclass under an alias": ("from dataclasses import dataclass as dc\n", "@dc(eq=False)"),
    "dataclasses imported under an alias": ("import dataclasses as dcs\n", "@dcs.dataclass"),
    "functools.total_ordering": ("import functools\n", "@functools.total_ordering"),
    "total_ordering": ("from functools import total_ordering\n", "@total_ordering"),
    "typing.final": ("import typing\n", "@typing.final"),
    "final": ("from typing import final\n", "@final"),
    "runtime_checkable": ("from typing import runtime_checkable, Protocol\n", "@runtime_checkable"),
    "attr.s": ("import attr\n", "@attr.s"),
    "attr.s(auto_attribs=True)": ("import attr\n", "@attr.s(auto_attribs=True)"),
    "attr.attrs": ("import attr\n", "@attr.attrs(frozen=True)"),
    "attr.define": ("import attr\n", "@attr.define"),
    "attr.frozen": ("import attr\n", "@attr.frozen"),
    "frozen from attr": ("from attr import frozen\n", "@frozen"),
    "attrs.define": ("import attrs\n", "@attrs.define"),
    "attrs.frozen(...)": ("import attrs\n", "@attrs.frozen(slots=False)"),
    "a stack of listed ones": (
        "import functools\nfrom dataclasses import dataclass\n",
        "@functools.total_ordering\n@dataclass(order=False)",
    ),
}


@pytest.mark.parametrize("how", sorted(SAME_CLASS))
def test_a_class_with_only_decorators_that_return_the_same_class_stays_exact(make_repo, how):
    head, decorators = SAME_CLASS[how]
    graph = graph_of(make_repo, head, decorators)
    assert calls_made(graph) == EXACT, how
