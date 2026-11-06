"""callers of a decorated function lists the wrapper, which is what really calls it, and also the lines
that write its name and reach it through the wrapper, labelled, so a rename or a signature change sees
every line that executes the function."""

from conftest import FIXTURES

from ripple.indexer import build_graph
from ripple.queries import callers, impact

TIMED = """\
import functools

def timed(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper

def guarded(fn):
    def check(*args):
        return fn(*args)

    return check

@timed
def one():
    pass

@guarded
@timed
def stacked():
    pass

def use_one():
    one()
    one()

def use_stacked():
    stacked()

class Dock:
    @timed
    def unload(self):
        pass

def run_unload(dock: Dock):
    dock.unload()
"""


def through(result):
    return {c["symbol"]: c for c in result["through_wrapper"]}


def test_callers_still_lists_the_wrapper_as_the_caller(make_repo):
    g = build_graph(make_repo({"m.py": TIMED}))
    assert [c["symbol"] for c in callers(g, "m.one")["callers"]] == ["m.timed.wrapper"]


def test_the_lines_that_name_the_function_are_listed_as_through_the_wrapper(make_repo):
    g = build_graph(make_repo({"m.py": TIMED}))
    found = through(callers(g, "m.one"))
    assert list(found) == ["m.use_one"]
    assert found["m.use_one"]["call_sites"] == ["m.py:26", "m.py:27"]
    assert found["m.use_one"]["via_wrapper"] == "m.timed.wrapper"


def test_a_stack_lists_the_call_sites_with_the_top_wrapper(make_repo):
    g = build_graph(make_repo({"m.py": TIMED}))
    found = through(callers(g, "m.stacked"))
    assert list(found) == ["m.use_stacked"]
    assert found["m.use_stacked"]["via_wrapper"] == "m.guarded.check"


def test_a_decorated_method_lists_the_callers_of_the_method(make_repo):
    g = build_graph(make_repo({"m.py": TIMED}))
    assert list(through(callers(g, "m.Dock.unload"))) == ["m.run_unload"]


def test_an_undecorated_function_and_a_wrapper_have_nothing_through_a_wrapper(make_repo):
    g = build_graph(make_repo({"m.py": TIMED + "\ndef plain():\n    pass\n\ndef go():\n    plain()\n"}))
    assert callers(g, "m.plain")["through_wrapper"] == []
    assert callers(g, "m.timed.wrapper")["through_wrapper"] == []


def test_one_function_does_not_list_the_callers_of_another_that_shares_its_wrapper(make_repo):
    g = build_graph(make_repo({"m.py": TIMED}))
    assert "m.run_unload" not in through(callers(g, "m.one"))
    assert "m.use_one" not in through(callers(g, "m.Dock.unload"))


def test_impact_labels_the_caller_that_reaches_the_function_through_its_wrapper(make_repo):
    g = build_graph(make_repo({"m.py": TIMED}))
    entries = {e["symbol"]: e for entries in impact(g, "m.one")["by_file"].values() for e in entries}
    assert entries["m.timed.wrapper"].get("via_wrapper") is None
    assert entries["m.use_one"]["via_wrapper"] == "m.timed.wrapper"
    assert entries["m.use_one"]["distance"] == 2


def test_the_bench_warehouse_ship_lists_the_lines_that_call_it():
    g = build_graph(FIXTURES / "warehouse")
    found = through(callers(g, "warehouse.guards.ship"))
    assert sorted(found) == ["tests.test_guards.test_ship_direct", "warehouse.guards.run_ship"]
    assert {c["via_wrapper"] for c in found.values()} == {"warehouse.guards.counted.decorate.counter"}
