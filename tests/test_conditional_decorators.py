"""A decoration is modelled only when it is the one binding of the name.

A def written twice (in each branch of an if, or one after the other) is one symbol to ripple, and
which def the name is bound to when a call runs depends on the branch taken. Decorating only one of
them must not send every call to the wrapper.
"""

from conftest import callees_of, callers_of

from ripple.indexer import build_graph
from ripple.queries import impact, path

TRACED = """\
    import os

    def traced(fn):
        def wrapper(*args):
            return fn(*args)

        return wrapper

    def other():
        pass
    """


def reaches(graph, source, target):
    return path(graph, source, target)["path"] is not None


def test_a_function_decorated_in_one_branch_only_is_called_directly(make_repo):
    repo = make_repo(
        {
            "m.py": TRACED
            + """\

    if os.environ.get("TRACE"):
        @traced
        def handler(x):
            return other()
    else:
        def handler(x):
            return other()

    def serve(x):
        return handler(x)
    """
        }
    )
    g = build_graph(repo)
    assert callees_of(g, "m.serve") == {"m.handler"}
    assert callers_of(g, "m.traced.wrapper") == set()
    assert callees_of(g, "m.traced.wrapper") == set()
    assert reaches(g, "m.serve", "m.other")  # through handler, as at runtime in either branch


def test_the_undecorated_branch_first_is_the_same(make_repo):
    repo = make_repo(
        {
            "m.py": TRACED
            + """\

    if os.environ.get("TRACE"):
        def handler(x):
            return 1
    else:
        @traced
        def handler(x):
            return 2

    def serve(x):
        return handler(x)
    """
        }
    )
    g = build_graph(repo)
    assert callees_of(g, "m.serve") == {"m.handler"}
    assert callers_of(g, "m.traced.wrapper") == set()


def test_the_same_decorator_on_both_branches_is_applied_once_not_twice(make_repo):
    repo = make_repo(
        {
            "m.py": TRACED
            + """\

    if os.environ.get("TRACE"):
        @traced
        def handler(x):
            return 1
    else:
        @traced
        def handler(x):
            return 2

    def serve(x):
        return handler(x)
    """
        }
    )
    g = build_graph(repo)
    assert callees_of(g, "m.serve") == {"m.handler"}  # not through a wrapper of a wrapper


def test_a_function_defined_twice_in_a_row_with_a_decorator_on_the_later_one(make_repo):
    repo = make_repo(
        {
            "m.py": TRACED
            + """\

    def handler(x):
        return 1

    @traced
    def handler(x):
        return 2

    def serve(x):
        return handler(x)
    """
        }
    )
    g = build_graph(repo)
    assert callees_of(g, "m.serve") == {"m.handler"}


def test_a_decorated_function_defined_once_is_still_read(make_repo):
    repo = make_repo(
        {
            "m.py": TRACED
            + """\

    if os.environ.get("TRACE"):
        @traced
        def handler(x):
            return 1

    def serve(x):
        return handler(x)
    """
        }
    )
    g = build_graph(repo)
    assert callees_of(g, "m.serve") == {"m.traced.wrapper"}


def test_impact_of_something_the_conditional_function_calls_lists_the_caller_once_without_a_wrapper(make_repo):
    repo = make_repo(
        {
            "m.py": TRACED
            + """\

    if os.environ.get("TRACE"):
        @traced
        def handler(x):
            return other()
    else:
        def handler(x):
            return other()

    def serve(x):
        return handler(x)
    """
        }
    )
    g = build_graph(repo)
    listed = {e["symbol"] for entries in impact(g, "m.other")["by_file"].values() for e in entries}
    assert listed == {"m.handler", "m.serve"}
