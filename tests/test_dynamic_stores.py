"""A table or a partial held in a module's name is proven only when nothing can store another value there.

That includes stores whose name is computed (`for name, v in OVERRIDES.items(): setattr(mod, name, v)`),
monkeypatch.setattr and mock.patch, writes through a module's __dict__, and sys.modules. Any of
those words in any file leaves every table and partial call unresolved, with the reason (the words, and
every other spelling, are in test_store_tokens.py).
"""

import pytest
from conftest import callers_of

from ripple.indexer import build_graph

DISPATCH = """\
    from functools import partial

    def apply_tax(x, rate):
        return x * rate

    def apply_discount(x):
        return x - 1

    HANDLERS = {"discount": apply_discount}
    with_tax = partial(apply_tax, rate=2)

    def run_discount(x):
        return HANDLERS["discount"](x)

    def run_tax(x):
        return with_tax(x)
    """


def graph_with(make_repo, other, name="pkg/other.py"):
    return build_graph(make_repo({"pkg/__init__.py": "", "pkg/dispatch.py": DISPATCH, name: other}))


def proven(graph):
    return {
        "discount": "pkg.dispatch.apply_discount" in {e.callee for e in graph.edges if e.caller == "pkg.dispatch.run_discount"},
        "tax": "pkg.dispatch.apply_tax" in {e.callee for e in graph.edges if e.caller == "pkg.dispatch.run_tax"},
    }


def test_the_module_alone_proves_both(make_repo):
    g = build_graph(make_repo({"pkg/__init__.py": "", "pkg/dispatch.py": DISPATCH}))
    assert proven(g) == {"discount": True, "tax": True}


def test_setattr_on_the_module_with_a_name_from_a_table_leaves_both_unresolved(make_repo):
    other = """\
        from functools import partial

        from pkg import dispatch

        OVERRIDES = {"HANDLERS": {"discount": dispatch.apply_discount}, "with_tax": partial(dispatch.apply_tax, rate=3)}

        def install():
            for name, value in OVERRIDES.items():
                setattr(dispatch, name, value)
        """
    g = graph_with(make_repo, other)
    assert proven(g) == {"discount": False, "tax": False}
    reasons = {u.caller: u.reason for u in g.unresolved if u.caller.startswith("pkg.dispatch.run_")}
    assert "setattr" in reasons["pkg.dispatch.run_discount"] and "pkg/other.py:9" in reasons["pkg.dispatch.run_discount"]
    assert "setattr" in reasons["pkg.dispatch.run_tax"]


def test_delattr_with_a_computed_name_is_the_same(make_repo):
    other = "from pkg import dispatch\n\ndef drop(name):\n    delattr(dispatch, name)\n"
    assert proven(graph_with(make_repo, other)) == {"discount": False, "tax": False}


@pytest.mark.parametrize(
    "call, name",
    [
        ('monkeypatch.setattr(dispatch, "HANDLERS", {})', "discount"),
        ('monkeypatch.setattr(dispatch, "with_tax", len)', "tax"),
        ('monkeypatch.setattr("pkg.dispatch.HANDLERS", {})', "discount"),
        ('monkeypatch.delattr(dispatch, "with_tax")', "tax"),
        ('mock.patch.object(dispatch, "HANDLERS", {})', "discount"),
        ('mock.patch("pkg.dispatch.with_tax", len)', "tax"),
        ('patch("pkg.dispatch.HANDLERS", {})', "discount"),
        ('patch.object(dispatch, "with_tax", len)', "tax"),
        ('patch.dict("pkg.dispatch.HANDLERS", {})', "discount"),
    ],
)
def test_a_patch_of_a_constant_name_leaves_both_unresolved(make_repo, call, name):
    # Was: only the name patched was unresolved. A store token anywhere now leaves every table and
    # partial unresolved, because ripple no longer tries to tell which module or name a store lands on.
    other = f"from pkg import dispatch\n\ndef test_it(monkeypatch):\n    {call}\n"
    assert proven(graph_with(make_repo, other)) == {"discount": False, "tax": False}


def test_a_patch_with_a_name_ripple_cannot_read_leaves_both_unresolved(make_repo):
    other = "from pkg import dispatch\n\ndef test_it(monkeypatch, which):\n    monkeypatch.setattr(dispatch, which, None)\n"
    assert proven(graph_with(make_repo, other)) == {"discount": False, "tax": False}


@pytest.mark.parametrize(
    "body",
    [
        "vars(dispatch)[key] = value",
        "dispatch.__dict__[key] = value",
        "dispatch.__dict__.update(values)",
        "vars(dispatch).update(values)",
        "exec(code, dispatch.__dict__)",
        "del vars(dispatch)[key]",
    ],
)
def test_a_write_through_the_modules_dict_leaves_both_unresolved(make_repo, body):
    other = f"from pkg import dispatch\n\ndef sneak(key, value, values, code):\n    {body}\n"
    assert proven(graph_with(make_repo, other)) == {"discount": False, "tax": False}


@pytest.mark.parametrize(
    "body",
    [
        'sys.modules["pkg.dispatch"] = fake',
        "sys.modules[name] = fake",
        "sys.modules.update(fakes)",
        'monkeypatch.setitem(sys.modules, "pkg.dispatch", fake)',
        'mock.patch.dict(sys.modules, {"pkg.dispatch": fake})',
        'del sys.modules["pkg.dispatch"]',
    ],
)
def test_a_write_to_sys_modules_leaves_both_unresolved(make_repo, body):
    other = f"import sys\n\ndef sneak(name, fake, fakes, monkeypatch, mock):\n    {body}\n"
    assert proven(graph_with(make_repo, other)) == {"discount": False, "tax": False}


def test_a_store_on_self_counts_too(make_repo):
    # Was: `setattr(self, ...)` was exempt as a store on an instance. It is a token like any other: self can
    # be a module's holder (`setattr(self.module, k, v)` was the same text with one more word).
    other = """\
        class Config:
            def __init__(self, **values):
                for name, value in values.items():
                    setattr(self, name, value)
        """
    assert proven(graph_with(make_repo, other)) == {"discount": False, "tax": False}


def test_a_store_on_another_module_counts_too(make_repo):
    # Was: only the module the store lands on lost its table. Which module that is, ripple cannot prove.
    files = {
        "pkg/__init__.py": "",
        "pkg/dispatch.py": DISPATCH,
        "pkg/settings.py": "DEBUG = False\n",
        "pkg/other.py": "from pkg import settings\n\ndef install(name, value):\n    setattr(settings, name, value)\n",
    }
    assert proven(build_graph(make_repo(files))) == {"discount": False, "tax": False}


def test_a_module_that_stores_on_one_whose_table_it_imports_makes_that_table_unresolved(make_repo):
    # The table is imported by name into another module and called there: the module that holds it decides.
    files = {
        "pkg/__init__.py": "",
        "pkg/dispatch.py": DISPATCH,
        "pkg/user.py": "from pkg.dispatch import HANDLERS\n\ndef use(x):\n    return HANDLERS['discount'](x)\n",
        "pkg/other.py": "from pkg import dispatch\n\ndef install(name, value):\n    setattr(dispatch, name, value)\n",
    }
    g = build_graph(make_repo(files))
    assert callers_of(g, "pkg.dispatch.apply_discount") == set()
    assert [u.caller for u in g.unresolved if u.caller == "pkg.user.use"] == ["pkg.user.use"]
