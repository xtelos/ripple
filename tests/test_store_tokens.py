"""A module-level table or functools.partial is followed only when no file in the repo can store a value
on a module by any spelling: one token from STORE_TOKENS anywhere in any file (as a name, an attribute,
or in a string) leaves every table and partial call unresolved, with the token and where it is.

The rule is a token match, not a match of the shape of a call, so a store cannot be written in a form
the scan does not know: an alias, an attribute of self, a backslash continuation, a look-alike
Unicode letter, a file in another encoding. These are the stores that got past the form-matching
scan, each of which ran and replaced the partial.
"""

import pytest

from ripple.indexer import STORE_TOKENS, build_graph

TAIL = """\
    def run_discount(x):
        return HANDLERS["discount"](x)

    def run_tax(x):
        return with_tax(x)
    """
HEAD = """\
    from functools import partial

    def apply_tax(x, rate):
        return x * rate

    def apply_discount(x):
        return x - 1

    def audit_tax(x):
        return x + 100

    HANDLERS = {"discount": apply_discount}
    with_tax = partial(apply_tax, rate=2)

    """
FIRST_TAX = """\
    def run_tax(x):
        return with_tax(x)

    def run_discount(x):
        return HANDLERS["discount"](x)
    """
NO_TABLE_TAIL = """\
    def run_discount(x):
        return apply_discount(x)

    def run_tax(x):
        return with_tax(x)
    """
# The same module in three layouts: the answer must not depend on which proof runs first, or on there being a table.
HEAD = HEAD.rstrip(" ")  # the closing quotes leave one line of indent behind
LAYOUTS = {"both": HEAD + TAIL, "tax_first": HEAD + FIRST_TAX, "no_table": HEAD.replace("HANDLERS", "NOT_USED") + NO_TABLE_TAIL}


def dedented(text):
    import textwrap

    return textwrap.dedent(text)


def tax_and_discount(graph):
    callees = lambda caller: {e.callee for e in graph.edges if e.caller == caller}  # noqa: E731
    return (
        "pkg.dispatch.apply_tax" in callees("pkg.dispatch.run_tax"),
        "pkg.dispatch.apply_discount" in callees("pkg.dispatch.run_discount"),
    )


def build(make_repo, other, layout="both", bytes_file=None):
    root = make_repo(
        {"pkg/__init__.py": "", "pkg/defaults.py": "", "pkg/dispatch.py": LAYOUTS[layout], "pkg/over.py": other}
    )
    if bytes_file is not None:
        (root / "pkg" / "over.py").write_bytes(bytes_file)
    return build_graph(root)


# -- the control: the layouts prove both calls when nothing stores anything -----------------------------------


@pytest.mark.parametrize("layout", LAYOUTS)
def test_without_a_token_both_calls_are_proven(make_repo, layout):
    g = build(make_repo, "from pkg import dispatch\n\nX = dispatch.apply_tax\n", layout)
    tax, discount = tax_and_discount(g)
    assert tax and discount


@pytest.mark.parametrize(
    "text",
    [
        "dispatch_all = 1\npatched = 2\nmy_patch = 3\nvariables = 4\nexecutor = 5\nevaluate = 6\nretrieval = 7",
        "def varsity(): pass\nglobalsum = 1\nlocalsite = 2\nbuiltins_list = 3\nimportlib_like = 4\n_setattr = 5",
        "def set_attribute(a, b): pass\nunpatch = 1\n",
        "import sys\nprint(sys.argv, sys.exit)\nmodules_dir = 1\nmodules = []\nmodulesx = 2\n",
    ],
)
def test_a_word_that_only_contains_a_token_is_not_one(make_repo, text):
    g = build(make_repo, text)
    assert tax_and_discount(g) == (True, True)


# -- finding 1 (HIGH): a store on an attribute of self or cls --------------------------------------------------

SELF_ATTR = {
    "self_attr_setattr": """\
        from pkg import dispatch

        class Overrides:
            def __init__(self, module):
                self.module = module

            def apply(self, values):
                for k, v in values.items():
                    setattr(self.module, k, v)

        Overrides(dispatch).apply({"with_tax": dispatch.audit_tax})
        """,
    "self_attr_dict": """\
        from pkg import dispatch

        class Overrides:
            def __init__(self, module):
                self.module = module

            def apply(self, values):
                self.module.__dict__.update(values)

        Overrides(dispatch).apply({"with_tax": dispatch.audit_tax})
        """,
    "cls_attr_setattr": """\
        from pkg import dispatch

        class Plugin:
            target = dispatch

            @classmethod
            def install(cls, values):
                for k, v in values.items():
                    setattr(cls.target, k, v)

        Plugin.install({"with_tax": dispatch.audit_tax})
        """,
    "bare_self": """\
        class Config:
            def __init__(self, **values):
                for name, value in values.items():
                    setattr(self, name, value)
        """,
}


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("name", SELF_ATTR)
def test_finding_1_a_store_on_an_attribute_of_self_or_cls(make_repo, name, layout):
    g = build(make_repo, dedented(SELF_ATTR[name]), layout)
    tax, discount = tax_and_discount(g)
    assert not tax and (not discount or layout == "no_table")
    reason = next(u.reason for u in g.unresolved if u.caller == "pkg.dispatch.run_tax")
    assert "pkg/over.py" in reason and ("setattr" in reason or "__dict__" in reason), reason


# -- finding 2 (MED): a parameter or a local that shares a name with a module-level name -----------------------

SHADOW = {
    "param_shadow": """\
        from pkg import dispatch

        class Config:
            pass

        target = Config()

        def install(target, overrides):
            for k, v in overrides.items():
                setattr(target, k, v)

        install(dispatch, {"with_tax": dispatch.audit_tax})
        """,
    "param_shadow_module": """\
        from pkg import dispatch, defaults as target

        def install(target, overrides):
            for k, v in overrides.items():
                setattr(target, k, v)

        install(dispatch, {"with_tax": dispatch.audit_tax})
        """,
    "local_shadow_instance": """\
        import importlib

        class Registry:
            pass

        m = Registry()

        def install(path, overrides):
            m = importlib.import_module(path)
            for k, v in overrides.items():
                setattr(m, k, getattr(m, v))

        install("pkg.dispatch", {"with_tax": "audit_tax"})
        """,
}


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("name", SHADOW)
def test_finding_2_a_store_through_a_name_that_shadows_a_module_level_one(make_repo, name, layout):
    g = build(make_repo, dedented(SHADOW[name]), layout)
    tax, discount = tax_and_discount(g)
    assert not tax and (not discount or layout == "no_table")


# -- finding 3 (LOW): store forms the form-matching scan did not know -----------------------------------------

FORMS = {
    "patch_alias": 'from unittest.mock import patch as p\n\np("pkg.dispatch.with_tax", len).start()\n',
    "mock_alias": 'import unittest.mock as um\n\num.patch("pkg.dispatch.with_tax", len)\n',
    "object_setattr": "from pkg import dispatch\n\nfor k, v in {}.items():\n    object.__setattr__(dispatch, k, v)\n",
    "dunder_setattr": "from pkg import dispatch\n\nfor k, v in {}.items():\n    dispatch.__setattr__(k, v)\n",
    "alias_setattr": 's = setattr\ns(dispatch, "with_tax", len)\n',
    "partial_setattr": "import functools\nfrom pkg import dispatch\n\nput = functools.partial(setattr, dispatch)\n",
    "builtins_vars": "import builtins\nfrom pkg import dispatch\n\nbuiltins.vars(dispatch)['with_tax'] = len\n",
    "getattr_dict": "from pkg import dispatch\n\ngetattr(dispatch, '__dict__')['with_tax'] = len\n",
    "exec_other": 'from pkg import dispatch\n\nexec("d.with_tax = d.audit_tax", {"d": dispatch})\n',
    "eval_other": 'from pkg import dispatch\n\neval("d.__class__", {"d": dispatch})\n',
    "import_module": "import importlib\n\nm = importlib.import_module('pkg.dispatch')\n",
    "dunder_import": "m = __import__('pkg.dispatch')\n",
    "globals_call": "from pkg import dispatch\n\ndispatch.run_tax.__globals__['with_tax'] = len\n",
    "frame_globals": "import sys\n\nsys._getframe(1).f_globals['with_tax'] = len\n",
    "monkeypatch_fixture": "def test_it(monkeypatch):\n    monkeypatch.undo()\n",
    "mocker_name": "def test_it(mocker):\n    mocker.patch('pkg.dispatch.with_tax', len)\n",
    "patch_decorator": "from unittest import mock\n\n@mock.patch('pkg.dispatch.with_tax')\ndef test_it(m):\n    pass\n",
    "builtins_name": "import builtins\n\nb = builtins\n",
    "dunder_builtins": "b = __builtins__\n",
    "locals_call": "def f():\n    return locals()\n",
    "vars_call": "from pkg import dispatch\n\nvars(dispatch)['with_tax'] = len\n",
    "delattr_call": "from pkg import dispatch\n\ndelattr(dispatch, 'with_tax')\n",
    "dunder_delattr": "from pkg import dispatch\n\ndispatch.__delattr__('with_tax')\n",
    "dunder_dict": "from pkg import dispatch\n\ndispatch.__dict__.update({})\n",
    "sys_modules_store": "import sys\n\nsys.modules['pkg.dispatch'] = object()\n",
    "sys_modules_alias": "import sys as s\n\nm = s.modules\nm['pkg.dispatch'] = object()\n",
    "sys_modules_from": "from sys import modules as m\n\nm['pkg.dispatch'] = object()\n",
    "sys_modules_from_paren": "from sys import (\n    argv,\n    modules,\n)\n",
    "sys_modules_from_continued": "from sys import argv, \\\n    modules\n",
    "sys_modules_getattr": "import sys\n\ngetattr(sys, 'modules')['pkg.dispatch'] = object()\n",
    "sys_modules_dotted": "import importlib\n\nimportlib.sys.\\\n    modules.clear()\n",
}


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("name", FORMS)
def test_finding_3_a_store_form_the_scan_did_not_know(make_repo, name, layout):
    g = build(make_repo, FORMS[name], layout)
    tax, discount = tax_and_discount(g)
    assert not tax and (not discount or layout == "no_table")


@pytest.mark.parametrize("token", sorted(STORE_TOKENS))
def test_every_token_in_the_list_does_it_as_a_name(make_repo, token):
    g = build(make_repo, f"x = {token}\n")
    assert tax_and_discount(g) == (False, False)


def test_the_token_is_found_in_a_file_that_imports_nothing_from_the_module(make_repo):
    # Blunt on purpose: a file that cannot reach pkg.dispatch can still be called by one that can.
    g = build(make_repo, "def f(o, k, v):\n    setattr(o, k, v)\n")
    assert tax_and_discount(g) == (False, False)
    reason = next(u.reason for u in g.unresolved if u.caller == "pkg.dispatch.run_tax")
    assert "'setattr'" in reason and "pkg/over.py:2" in reason, reason


def test_a_token_in_a_comment_or_a_string_also_counts(make_repo):
    g = build(make_repo, "# we never call setattr here\nDOC = 'see vars()'\n")
    assert tax_and_discount(g) == (False, False)


# -- finding 4 (LOW): spellings the byte filter did not see, and an answer that depended on which proof ran first


SPELLINGS = {
    "myself_dict": "from pkg import dispatch\n\ndef put(myself, k, v):\n    myself.__dict__[k] = v\n",
    "backslash_setattr": "from pkg import dispatch\n\nfor k, v in {}.items():\n    setattr \\\n        (dispatch, k, v)\n",
    "nfkc_setattr": "from pkg import dispatch\n\nfor k, v in {}.items():\n    ｓｅｔａｔｔｒ(dispatch, k, v)\n",
    "nfkc_vars": "from pkg import dispatch\n\nfor k, v in {}.items():\n    ｖａｒｓ(dispatch)[k] = v\n",
    "nfkc_attribute": "from pkg import dispatch\n\ndispatch.ｖａｒｓ = 1\nimport builtins\nbuiltins.ｓｅｔａｔｔｒ(dispatch, 'with_tax', len)\n",
}


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("name", SPELLINGS)
def test_finding_4_a_spelling_the_byte_filter_did_not_see(make_repo, name, layout):
    g = build(make_repo, SPELLINGS[name], layout)
    tax, discount = tax_and_discount(g)
    assert not tax and (not discount or layout == "no_table")


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize(
    "encoded",
    [
        b"# coding: utf-7\nfrom pkg import dispatch\nfor k, v in {}.items():\n    +AHM-etattr(dispatch, k, v)\n",
        b"\xef\xbb\xbffrom pkg import dispatch\nfor k, v in {}.items():\n    setattr(dispatch, k, v)\n",
        b"# -*- coding: latin-1 -*-\n# caf\xe9\nfrom pkg import dispatch\nvars(dispatch)['with_tax'] = len\n",
        b"# vim: set fileencoding=utf-7 :\nx = 1\n+AF8AXw-dict+AF8AXw-\n",
    ],
    ids=["utf7_cookie", "utf8_bom", "latin1_cookie", "utf7_dunder_dict"],
)
def test_finding_4_a_file_in_another_encoding_is_read_as_python_reads_it(make_repo, encoded, layout):
    g = build(make_repo, "", layout, bytes_file=encoded)
    tax, discount = tax_and_discount(g)
    assert not tax and (not discount or layout == "no_table")


def test_a_file_that_cannot_be_decoded_may_store(make_repo):
    # Invalid UTF-8 in a comment parses never (the interpreter rejects it too): the unparsed-file rule and
    # the token rule agree, and neither lets a table through.
    g = build(make_repo, "", bytes_file=b"x = 1  # \xff\xfe broken\n")
    assert tax_and_discount(g) == (False, False)


def test_a_repo_without_any_token_proves_both_in_every_layout_and_order(make_repo):
    for layout in LAYOUTS:
        g = build(make_repo, "x = 1\n", layout)
        assert tax_and_discount(g) == (True, True)
