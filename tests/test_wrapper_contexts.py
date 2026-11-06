"""One wrapper body serves every function its decorator is applied to, but each decoration wraps one.

The wrapper's `fn` is a different function in each decoration, so what reaches the wrapper through
one decorated name never continues into another. impact and path must not connect them.
"""

from conftest import FIXTURES, callers_of

from ripple.indexer import build_graph
from ripple.queries import callers, impact, path

TIMED = """\
    import functools

    def timed(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper
    """


def impacted(graph, symbol, depth=6):
    result = impact(graph, symbol, depth=depth)
    return {e["symbol"] for entries in result["by_file"].values() for e in entries} | {t["symbol"] for t in result["tests"]}


def hops(graph, source, target):
    found = path(graph, source, target)["path"]
    return [hop["symbol"] for hop in found] if found else None


def test_a_wrapper_shared_by_two_functions_does_not_connect_their_callers(make_repo):
    # use_one reaches one, and use_two reaches two, through the same wrapper body. Neither reaches the other.
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    @timed
    def one():
        pass

    @timed
    def two():
        pass

    def use_one():
        one()

    def use_two():
        two()
    """
        }
    )
    g = build_graph(repo)
    assert callers_of(g, "m.timed.wrapper") == {"m.use_one", "m.use_two"}  # the body really has both callers
    assert hops(g, "m.use_one", "m.one") == ["m.use_one", "m.timed.wrapper", "m.one"]
    assert hops(g, "m.use_one", "m.two") is None
    assert hops(g, "m.use_two", "m.one") is None
    assert impacted(g, "m.one") == {"m.timed.wrapper", "m.use_one"}
    assert impacted(g, "m.two") == {"m.timed.wrapper", "m.use_two"}


def test_a_decorated_method_and_a_decorated_function_share_a_wrapper_without_sharing_callers(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    @timed
    def ship():
        pass

    class Dock:
        @timed
        def unload(self):
            pass

    def run_ship():
        ship()

    def run_unload(dock: Dock):
        dock.unload()
    """
        }
    )
    g = build_graph(repo)
    assert hops(g, "m.run_unload", "m.ship") is None
    assert impacted(g, "m.ship") == {"m.timed.wrapper", "m.run_ship"}
    assert impacted(g, "m.Dock.unload") == {"m.timed.wrapper", "m.run_unload"}


def test_a_stack_of_two_wrappers_keeps_each_function_apart(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    def guarded(fn):
        def check(*args):
            return fn(*args)

        return check

    @guarded
    @timed
    def one():
        pass

    @timed
    def two():
        pass

    def use_one():
        one()

    def use_two():
        two()
    """
        }
    )
    g = build_graph(repo)
    assert impacted(g, "m.one") == {"m.timed.wrapper", "m.guarded.check", "m.use_one"}
    assert impacted(g, "m.two") == {"m.timed.wrapper", "m.use_two"}
    assert hops(g, "m.use_two", "m.one") is None
    assert hops(g, "m.use_one", "m.one") == ["m.use_one", "m.guarded.check", "m.timed.wrapper", "m.one"]


def test_a_query_that_starts_inside_the_shared_wrapper_still_reaches_every_function(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    @timed
    def one():
        pass

    @timed
    def two():
        pass
    """
        }
    )
    g = build_graph(repo)
    assert impacted(g, "m.timed.wrapper") == set()
    assert hops(g, "m.timed.wrapper", "m.two") == ["m.timed.wrapper", "m.two"]
    assert hops(g, "m.timed.wrapper", "m.one") == ["m.timed.wrapper", "m.one"]


def test_a_caller_of_the_wrapper_body_is_still_listed_for_the_body(make_repo):
    repo = make_repo(
        {
            "m.py": TIMED
            + """\

    @timed
    def one():
        pass

    def use_one():
        one()
    """
        }
    )
    g = build_graph(repo)
    assert [c["symbol"] for c in callers(g, "m.timed.wrapper")["callers"]] == ["m.use_one"]
    assert [c["symbol"] for c in callers(g, "m.one")["callers"]] == ["m.timed.wrapper"]


def test_the_bench_warehouse_run_unload_never_reaches_ship():
    g = build_graph(FIXTURES / "warehouse")
    assert hops(g, "warehouse.guards.run_unload", "warehouse.guards.ship") is None
    reached = impacted(g, "warehouse.guards.ship", depth=4)
    assert "warehouse.guards.run_unload" not in reached and "tests.test_guards.test_unload" not in reached
    assert {"warehouse.guards.run_ship", "tests.test_guards.test_ship"} <= reached
