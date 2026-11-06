"""A file ripple cannot parse may change a table or a partial, and ripple cannot see how.

With any such file in the repo a table or a partial is not proven, and the reason names the file.
"""

from ripple.indexer import build_graph

DISPATCH = """\
from functools import partial

def apply_tax(x, rate):
    return x * rate

def apply_discount(x):
    return x - 1

def apply_audit(x):
    return x

HANDLERS = {"discount": apply_discount}
with_tax = partial(apply_tax, rate=2)

def run_discount(x):
    return HANDLERS["discount"](x)

def run_tax(x):
    return with_tax(x)
"""

# Valid on a newer Python (PEP 758: no parentheses around two exceptions), a syntax error before 3.14.
LATE = """\
from pkg import dispatch

try:
    import fast
except ImportError, ModuleNotFoundError:
    dispatch.HANDLERS["discount"] = dispatch.apply_audit
    dispatch.with_tax = dispatch.apply_audit
"""

BROKEN = "from pkg import dispatch\n\ndef broken(:\n    dispatch.HANDLERS['discount'] = dispatch.apply_audit\n"


def callees(graph, caller):
    return {e.callee for e in graph.edges if e.caller == caller}


def test_without_an_unparsable_file_both_are_proven(make_repo):
    g = build_graph(make_repo({"pkg/__init__.py": "", "pkg/dispatch.py": DISPATCH}))
    assert "pkg.dispatch.apply_discount" in callees(g, "pkg.dispatch.run_discount")
    assert "pkg.dispatch.apply_tax" in callees(g, "pkg.dispatch.run_tax")


def test_an_unparsable_file_leaves_a_table_and_a_partial_unresolved(make_repo):
    g = build_graph(make_repo({"pkg/__init__.py": "", "pkg/dispatch.py": DISPATCH, "pkg/broken.py": BROKEN}))
    assert g.parse_errors == ["pkg/broken.py"]
    assert callees(g, "pkg.dispatch.run_discount") == set()
    assert callees(g, "pkg.dispatch.run_tax") == set()
    reasons = {u.caller: u.reason for u in g.unresolved if u.caller.startswith("pkg.dispatch.run_")}
    assert "pkg/broken.py could not be parsed" in reasons["pkg.dispatch.run_discount"]
    assert "pkg/broken.py could not be parsed" in reasons["pkg.dispatch.run_tax"]


def test_a_file_that_is_valid_only_on_a_newer_python_is_not_trusted_to_leave_them_alone(make_repo):
    g = build_graph(make_repo({"pkg/__init__.py": "", "pkg/dispatch.py": DISPATCH, "pkg/late.py": LATE}))
    assert callees(g, "pkg.dispatch.run_discount") == set()
    assert callees(g, "pkg.dispatch.run_tax") == set()
