"""Calls made on the result of a call: make().run(), Foo().run(), a().b().c()."""

from conftest import callers_of

from ripple.indexer import build_graph


def unresolved(graph):
    return sorted((u.text, u.reason) for u in graph.unresolved)


SHAPES = """\
    class Base:
        def run(self):
            pass

    class Sub(Base):
        def run(self):
            pass

    def make() -> Base:
        return Base()

    def make_string() -> "Base":
        return Base()

    def make_optional() -> Base | None:
        return None
    """


def test_a_call_on_a_function_result_follows_its_return_annotation(make_repo):
    repo = make_repo(
        {
            "m.py": SHAPES
            + """\

    def a():
        make().run()

    def b():
        make_string().run()

    def c():
        make_optional().run()
    """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.Base.run") == {"m.a", "m.b", "m.c"}
    assert g.unresolved == []


def test_a_return_annotation_leaves_room_for_a_subclass_but_a_constructor_does_not(make_repo):
    repo = make_repo(
        {
            "m.py": SHAPES
            + """\

    def annotated():
        make().run()

    def constructed():
        Base().run()
    """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.Base.run") == {"m.annotated", "m.constructed"}
    assert callers_of(g, "m.Sub.run") == {"m.annotated"}  # Base() is exactly a Base


def test_a_constructor_that_may_return_a_subclass_keeps_the_override(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class Base:
                    def __new__(cls):
                        return object.__new__(Sub)

                    def run(self):
                        pass

                class Sub(Base):
                    def run(self):
                        pass

                def f():
                    Base().run()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.Sub.run") == {"m.f"}


def test_chains_of_calls_methods_and_classmethods(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class Svc:
                    def place(self):
                        pass

                    def current(self) -> "Svc":
                        return self

                    @classmethod
                    def fresh(cls) -> "Svc":
                        return cls()

                    def helper(self):
                        self.current().place()

                def one():
                    Svc().current().place()

                def two():
                    Svc.fresh().place()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.Svc.place") == {"m.Svc.helper", "m.one", "m.two"}
    assert callers_of(g, "m.Svc.current") == {"m.Svc.helper", "m.one"}


def test_a_method_called_on_an_attribute_of_a_call_result(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class Engine:
                    def start(self):
                        pass

                class Car:
                    def __init__(self):
                        self.engine = Engine()

                def f():
                    Car().engine.start()
                """
        }
    )
    assert callers_of(build_graph(repo), "m.Engine.start") == {"m.f"}


def test_the_result_of_a_function_without_a_return_annotation_is_not_inferred(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class Thing:
                    def go(self):
                        pass

                def make():
                    return Thing()

                def f():
                    make().go()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.Thing.go") == set()
    assert unresolved(g) == [
        ("make().go", "the callee is the result of a call to a function with no return annotation")
    ]


def test_a_return_annotation_that_names_no_class_is_reported_as_such(make_repo):
    repo = make_repo({"m.py": "def make() -> list[int] | dict:\n    pass\n\ndef f():\n    make().go()\n"})
    assert unresolved(build_graph(repo)) == [
        (
            "make().go",
            "the callee is the result of a call whose return annotation does not name a class ripple can follow",
        )
    ]


def test_results_of_builtin_types_and_stdlib_classes_are_outside_the_repo(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                from pathlib import Path

                class Mine:
                    def strip(self):
                        pass

                    def exists(self):
                        pass

                def f(x):
                    str(x).strip()
                    Path(".").exists()
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.Mine.strip") == set() and callers_of(g, "m.Mine.exists") == set()
    assert g.unresolved == []


def test_the_result_of_a_function_from_outside_the_repo_is_not_assumed_to_be_a_class(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                import requests

                def f():
                    requests.get("u").json()
                """
        }
    )
    assert unresolved(build_graph(repo)) == [
        (
            "requests.get('u').json",
            "the callee is the result of a call into code outside the repo, not proven to build a class",
        )
    ]


def test_type_of_x_and_super_with_arguments_reach_repo_code(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class A:
                    def build(self):
                        pass

                class B(A):
                    def build(self):
                        super(B, self).build()
                        type(self).helper()

                    @classmethod
                    def helper(cls):
                        pass
                """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.A.build") == {"m.B.build"}
    assert [u.text for u in g.unresolved] == ["type(self).helper"]


def test_calling_a_call_result_is_calling_an_instance(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                class Handler:
                    pass

                def make() -> Handler:
                    return Handler()

                def f():
                    make()()
                """
        }
    )
    assert unresolved(build_graph(repo)) == [("make()", "calls an instance (its __call__ is not followed)")]


def test_a_subscripted_callee_and_a_computed_one_say_so(make_repo):
    repo = make_repo(
        {
            "m.py": """\
                TABLE = {}

                def f(k, a, b):
                    TABLE[k]()
                    (a or b)()
                """
        }
    )
    assert unresolved(build_graph(repo)) == [
        ("TABLE[k]", "the callee is looked up by subscript (a dict, list or registry)"),
        ("a or b", "the callee is computed at runtime (a lambda, a conditional expression or similar)"),
    ]
