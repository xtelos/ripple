"""Calls written in annotations: made by the scope that holds the def or class."""

from conftest import callers_of

from ripple.indexer import build_graph

RULE = """\
    from typing import Annotated

    def Field(gt=0):
        return gt
    """


def test_a_call_inside_annotated_on_a_class_attribute_is_the_class_bodys(make_repo):
    repo = make_repo({"m.py": RULE + "\n    class Model:\n        qty: Annotated[int, Field(gt=0)]\n"})
    g = build_graph(repo)
    assert callers_of(g, "m.Field") == {"m.Model"}
    assert [e.kind for e in g.edges if e.callee == "m.Field"] == ["annotation"]


def test_calls_in_parameter_and_return_annotations_belong_to_the_enclosing_scope(make_repo):
    repo = make_repo(
        {
            "m.py": RULE
            + """\

    def top(x: Annotated[int, Field(gt=1)]) -> Annotated[int, Field(gt=2)]:
        return x

    class Box:
        def put(self, x: Annotated[int, Field(gt=3)], *rest: Annotated[int, Field(gt=4)], **kw: Field(gt=5)):
            pass

        def get(self) -> Annotated[int, Field(gt=6)]:
            pass
    """
        }
    )
    g = build_graph(repo)
    edges = [(e.caller, e.line) for e in g.edges if e.callee == "m.Field"]
    assert {caller for caller, _ in edges} == {"m", "m.Box"}  # not m.top, m.Box.put or m.Box.get
    assert sorted(caller for caller, _ in edges) == ["m", "m", "m.Box", "m.Box", "m.Box", "m.Box"]


def test_an_annotated_variable_in_a_function_is_never_evaluated(make_repo):
    repo = make_repo({"m.py": RULE + "\n    def f():\n        v: Annotated[int, Field(gt=0)] = 1\n        return v\n"})
    assert callers_of(build_graph(repo), "m.Field") == set()


def test_an_annotated_variable_at_module_level_is_evaluated(make_repo):
    repo = make_repo({"m.py": RULE + "\n    LIMIT: Annotated[int, Field(gt=0)] = 1\n"})
    assert callers_of(build_graph(repo), "m.Field") == {"m"}


def test_the_future_annotations_import_still_attributes_the_call_to_the_scope_that_writes_it(make_repo):
    # The expression runs later, when pydantic or get_type_hints evaluates it. It is still
    # a call written in the class body, and the edge says it is an annotation.
    repo = make_repo(
        {"m.py": "    from __future__ import annotations\n" + RULE + "\n    class Model:\n        qty: Annotated[int, Field(gt=0)]\n"}
    )
    assert callers_of(build_graph(repo), "m.Field") == {"m.Model"}


def test_a_string_annotation_is_not_parsed_for_calls(make_repo):
    repo = make_repo({"m.py": RULE + "\n    class Model:\n        qty: 'Annotated[int, Field(gt=0)]'\n"})
    assert callers_of(build_graph(repo), "m.Field") == set()


def test_defaults_and_decorator_arguments_are_still_the_enclosing_scopes(make_repo):
    repo = make_repo(
        {
            "m.py": RULE
            + """\

    def tag(x):
        return lambda fn: fn

    @tag(Field(gt=1))
    def f(a=Field(gt=2)):
        pass
    """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.Field") == {"m"}
    assert sorted(e.kind for e in g.edges if e.callee == "m.Field") == ["call", "call"]
