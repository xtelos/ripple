"""TABLE[key](x) and `for f in TABLE: f(x)` are proven only for a table of plain function names that
no code in the repo changes, hands on or rebinds. Every other use of the table leaves the call unresolved."""

import textwrap

import pytest
from conftest import callers_of

from ripple.indexer import build_graph

FUNCS = """\
    def f(x):
        return x

    def g(x):
        return x

    def h(x):
        return x

    class K:
        def m(self, x):
            return x
    """


def repo_graph(make_repo, source, **more):
    files = {"m.py": textwrap.dedent(FUNCS) + textwrap.dedent(source)}
    files.update({name: textwrap.dedent(text) for name, text in more.items()})
    return build_graph(make_repo(files))


def reasons(graph, caller):
    return [u.reason for u in graph.unresolved if u.caller == caller]


# -- what is proven ---------------------------------------------------------


def test_a_constant_key_of_a_dict_nothing_changes_is_a_call_of_that_entry(make_repo):
    g = repo_graph(make_repo, 'T = {"a": f, "b": g}\n\ndef use(x):\n    return T["a"](x)\n')
    assert callers_of(g, "m.f") == {"m.use"}
    assert callers_of(g, "m.g") == set()


def test_an_index_of_a_list_or_tuple_including_a_negative_one(make_repo):
    src = "L = [f, g]\nU = (g, h)\n\ndef use(x):\n    return L[0](x), U[-1](x)\n"
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.f") == {"m.use"}
    assert callers_of(g, "m.g") == set()
    assert callers_of(g, "m.h") == {"m.use"}


def test_get_with_one_constant_key_is_the_same_lookup(make_repo):
    g = repo_graph(make_repo, 'T = {"a": f}\n\ndef use(x):\n    return T.get("a")(x)\n')
    assert callers_of(g, "m.f") == {"m.use"}


def test_get_with_a_default_is_not_followed(make_repo):
    g = repo_graph(make_repo, 'T = {"a": f}\n\ndef use(x):\n    return T.get("a", g)(x)\n')
    assert callers_of(g, "m.f") == set() and callers_of(g, "m.g") == set()


def test_a_local_assigned_an_entry_then_called(make_repo):
    g = repo_graph(make_repo, 'T = {"a": f}\n\ndef use(x):\n    fn = T["a"]\n    return fn(x)\n')
    assert callers_of(g, "m.f") == {"m.use"}


def test_a_local_assigned_two_different_entries_is_not_followed(make_repo):
    src = 'T = {"a": f, "b": g}\n\ndef use(x, c):\n    fn = T["a"]\n    if c:\n        fn = T["b"]\n    return fn(x)\n'
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.f") == set() and callers_of(g, "m.g") == set()


def test_a_local_assigned_an_entry_and_something_else_is_not_followed(make_repo):
    src = 'T = {"a": f}\n\ndef use(x, other):\n    fn = T["a"]\n    fn = other\n    return fn(x)\n'
    assert callers_of(repo_graph(make_repo, src), "m.f") == set()


def test_a_loop_over_a_tuple_or_list_calls_every_entry(make_repo):
    src = "S = (f, g)\nL = [h]\n\ndef use(x):\n    for fn in S:\n        x = fn(x)\n    for fn2 in L:\n        x = fn2(x)\n    return x\n"
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.f") == {"m.use"} and callers_of(g, "m.g") == {"m.use"}
    assert callers_of(g, "m.h") == {"m.use"}


def test_a_loop_over_values_or_items_of_a_dict(make_repo):
    src = 'T = {"a": f, "b": g}\n\ndef one(x):\n    for fn in T.values():\n        fn(x)\n\ndef two(x):\n    for k, fn in T.items():\n        fn(x)\n'
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.f") == {"m.one", "m.two"} and callers_of(g, "m.g") == {"m.one", "m.two"}


def test_a_table_with_the_same_function_twice_and_a_method_entry(make_repo):
    src = "S = (f, f, K.m)\n\ndef use(x):\n    for fn in S:\n        fn(x)\n"
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.f") == {"m.use"} and callers_of(g, "m.K.m") == {"m.use"}


def test_an_annotated_table(make_repo):
    g = repo_graph(make_repo, 'T: dict = {"a": f}\n\ndef use(x):\n    return T["a"](x)\n')
    assert callers_of(g, "m.f") == {"m.use"}


def test_a_table_read_from_another_module_by_import_alias_and_re_export(make_repo):
    other = {
        "pkg/__init__.py": "from .tab import T\n",
        "pkg/tab.py": "from m import f, g\n\nT = {'a': f}\n",
        "a.py": "import pkg\nfrom pkg import T\nimport pkg.tab as tab\n\ndef one(x):\n    return T['a'](x)\n\ndef two(x):\n    return pkg.T['a'](x)\n\ndef three(x):\n    return tab.T['a'](x)\n",
    }
    g = repo_graph(make_repo, "", **other)
    assert callers_of(g, "m.f") == {"a.one", "a.two", "a.three"}


def test_a_table_entry_that_is_decorated_calls_what_the_decorator_returned(make_repo):
    src = """
    def deco(fn):
        def inner(*a):
            return fn(*a)
        return inner

    @deco
    def d(x):
        return x

    T = {"a": d}

    def use(x):
        return T["a"](x)
    """
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.deco.inner") == {"m.use"}


# -- what leaves a table unresolved -------------------------------------------

MUTATIONS = {
    "item assignment": "def tweak():\n    T['b'] = g\n",
    "item assignment at module level": "T['b'] = g\n",
    "update": "def tweak():\n    T.update({'b': g})\n",
    "pop": "def tweak():\n    T.pop('a')\n",
    "setdefault": "def tweak():\n    T.setdefault('b', g)\n",
    "del": "def tweak():\n    del T['a']\n",
    "augmented assignment": "def tweak():\n    T['a'] += 1\n",
    "passed to a call": "def tweak():\n    register(T)\n",
    "len() of it": "def tweak():\n    return len(T)\n",
    "alias": "U = T\n",
    "alias in a function, then changed": "def tweak():\n    u = T\n    u['b'] = g\n",
    "returned": "def tweak():\n    return T\n",
    "stored in a container": "def tweak():\n    return [T]\n",
    "stored on an object": "def tweak(o):\n    o.table = T\n",
    "global rebinding": "def tweak():\n    global T\n    T = {'a': g}\n",
    "assigned again": "T = {'a': g}\n",
    "assigned again in a branch": "if __name__:\n    T = {'a': g}\n",
    "loop target": "for T in []:\n    pass\n",
    "dict() of it": "V = dict(T)\n",
    "getattr by name": "def tweak():\n    return getattr(__import__('m'), 'T')\n",
    "in a condition": "def tweak():\n    if T:\n        pass\n",
    "globals lookup": "def tweak():\n    return globals()['T']\n",
}


@pytest.mark.parametrize("how", sorted(MUTATIONS))
def test_a_table_that_is_used_in_any_other_way_is_not_followed(make_repo, how):
    src = 'T = {"a": f}\n\ndef use(x):\n    return T["a"](x)\n\n' + MUTATIONS[how]
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.f") == set(), how
    assert reasons(g, "m.use"), how


@pytest.mark.parametrize("how", sorted(MUTATIONS))
def test_the_same_uses_in_another_module_are_just_as_unsafe(make_repo, how):
    body = MUTATIONS[how].replace("T", "m.T").replace("global m.T", "global T").replace("__import__('m'), 'm.T'", "__import__('m'), 'T'")
    body = body.replace("'m.T'", "'T'").replace("globals()['m.T']", "globals()['T']")
    src = 'T = {"a": f}\n\ndef use(x):\n    return T["a"](x)\n'
    g = repo_graph(make_repo, src, **{"n.py": "import m\n\n" + body})
    assert callers_of(g, "m.f") == set(), how


def test_a_table_imported_under_another_name_that_is_changed_there(make_repo):
    src = 'T = {"a": f}\n\ndef use(x):\n    return T["a"](x)\n'
    g = repo_graph(make_repo, src, **{"n.py": "from m import T as U\n\nU['b'] = 1\n"})
    assert callers_of(g, "m.f") == set()


def test_an_alias_of_an_alias_is_followed_too(make_repo):
    src = 'T = {"a": f}\n\ndef use(x):\n    return T["a"](x)\n'
    other = {"n.py": "from m import T as U\n", "p.py": "from n import U as V\n\nV.clear()\n"}
    assert callers_of(repo_graph(make_repo, src, **other), "m.f") == set()


def test_a_module_that_uses_globals_or_exec_or_a_star_import_is_not_followed(make_repo):
    base = 'T = {"a": f}\n\ndef use(x):\n    return T["a"](x)\n\n'
    assert callers_of(repo_graph(make_repo, base + "def sneak(s):\n    exec(s)\n"), "m.f") == set()
    assert callers_of(repo_graph(make_repo, "from outside import *\n" + base), "m.f") == set()


def test_a_table_entry_that_is_not_a_plain_function_name_is_not_followed(make_repo):
    src = 'T = {"a": lambda x: x, "b": K, "c": f(1), "d": g}\n\ndef use(x):\n    return T["a"](x), T["b"](x), T["c"](x)\n'
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.K.__init__") == set()
    assert len(reasons(g, "m.use")) == 3


def test_a_key_the_table_does_not_have_is_not_followed(make_repo):
    g = repo_graph(make_repo, 'T = {"a": f}\nL = [f]\n\ndef use(x):\n    return T["z"](x), L[3](x), L["a"](x)\n')
    assert callers_of(g, "m.f") == set()
    assert len(reasons(g, "m.use")) == 3


def test_a_table_with_a_duplicate_or_computed_key_or_unpacking_is_not_followed(make_repo):
    src = 'D = {"a": f, "a": g}\nE = {**{"a": f}}\nF = {"a" + "b": f}\nG = [*[f]]\n\ndef use(x):\n    return D["a"](x), E["a"](x), F["ab"](x), G[0](x)\n'
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.f") == set() and callers_of(g, "m.g") == set()


def test_a_variable_key_is_not_followed(make_repo):
    g = repo_graph(make_repo, 'T = {"a": f}\n\ndef use(x, k):\n    return T[k](x)\n')
    assert callers_of(g, "m.f") == set()


def test_a_loop_over_the_keys_of_a_dict_is_not_a_loop_over_functions(make_repo):
    g = repo_graph(make_repo, 'T = {"a": f}\n\ndef use(x):\n    for fn in T:\n        fn(x)\n')
    assert callers_of(g, "m.f") == set()


def test_values_of_a_list_is_not_followed(make_repo):
    g = repo_graph(make_repo, "T = [f]\n\ndef use(x):\n    for fn in T.values():\n        fn(x)\n")
    assert callers_of(g, "m.f") == set()


def test_a_loop_whose_variable_is_assigned_elsewhere_is_not_followed(make_repo):
    src = """
    S = (f, g)

    def one(x):
        for fn in S:
            fn = wrap(fn)
            fn(x)

    def two(x, other):
        for fn in S:
            fn(x)
        for fn in other:
            pass

    def three(x):
        for fn in S:
            pass
        fn(x)

    def four(x):
        for fn in S:
            def later():
                return fn(x)
            later()

    def five(x):
        for fn in S:
            pass
        else:
            fn(x)

    def six(x):
        for fn in S:
            list(lambda: fn(x))

    def seven(x):
        for fn in S:
            (fn(x) for _ in range(3))
    """
    g = repo_graph(make_repo, src)
    assert callers_of(g, "m.f") == set()
    assert len(reasons(g, "m.one")) + len(reasons(g, "m.two")) + len(reasons(g, "m.three")) >= 3


def test_a_loop_variable_that_is_nonlocal_or_global_is_not_followed(make_repo):
    src = "S = (f, g)\n\ndef use(x):\n    global fn\n    for fn in S:\n        fn(x)\n"
    assert callers_of(repo_graph(make_repo, src), "m.f") == set()


def test_an_empty_table_loop_is_not_followed(make_repo):
    g = repo_graph(make_repo, "S = ()\n\ndef use(x):\n    for fn in S:\n        fn(x)\n")
    assert reasons(g, "m.use")


def test_a_name_that_is_not_a_table_keeps_its_old_reason(make_repo):
    src = "def use(d, x):\n    for fn in d:\n        fn(x)\n    d['a'](x)\n    d.get('a')(x)\n    y = d['a']\n    y.go()\n"
    g = repo_graph(make_repo, src)
    texts = reasons(g, "m.use")
    assert "the receiver is a loop variable" in texts
    assert "the callee is looked up by subscript (a dict, list or registry)" in texts
    assert "the receiver is a local assigned from an expression ripple cannot type" in texts
