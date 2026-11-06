"""A name that holds functools.partial(f, ...) calls f: proven only when nothing else could be in the name."""

import textwrap

from conftest import callees_of, callers_of

from ripple.indexer import build_graph

BASE = """\
    import functools
    from functools import partial

    def f(a, b):
        return a + b

    def g(a, b):
        return a - b

    class K:
        def __init__(self, a, b):
            pass

        def step(self, a, b):
            return a

    class L(K):
        def step(self, a, b):
            return b
    """


def graph_of(make_repo, body, **more):
    return build_graph(make_repo({"m.py": textwrap.dedent(BASE) + textwrap.dedent(body), **more}))


def reasons(graph, caller):
    return [u.reason for u in graph.unresolved if u.caller == caller]


def test_a_local_partial_of_a_function_calls_the_function(make_repo):
    g = graph_of(make_repo, "\ndef use(x):\n    p = partial(f, 1)\n    return p(x)\n")
    assert callers_of(g, "m.f") == {"m.use"}


def test_functools_dot_partial_and_an_alias_of_functools_count(make_repo):
    body = """
    import functools as ft

    def use(x):
        a = functools.partial(f, 1)
        b = ft.partial(g, 1)
        return a(x) + b(x)
    """
    g = graph_of(make_repo, body)
    assert callers_of(g, "m.f") == {"m.use"}
    assert callers_of(g, "m.g") == {"m.use"}


def test_a_module_level_partial_is_followed_from_another_module(make_repo):
    body = "\nT = partial(f, 1)\n"
    other = """
    import m
    from m import T

    def one(x):
        return T(x)

    def two(x):
        return m.T(x)
    """
    g = graph_of(make_repo, body, **{"n.py": other})
    assert callers_of(g, "m.f") == {"n.one", "n.two"}


def test_a_partial_of_a_bound_method_reaches_the_method_and_its_overrides(make_repo):
    body = """
    class Runner:
        def __init__(self):
            self.k = K(1, 2)

        def go(self, x):
            p = partial(self.k.step, 1)
            return p(x)
    """
    g = graph_of(make_repo, body.replace("self.k = K(1, 2)", "self.k: K = K(1, 2)"))
    assert callers_of(g, "m.K.step") == {"m.Runner.go"}
    assert callers_of(g, "m.L.step") == {"m.Runner.go"}  # the value could be an L


def test_a_partial_of_a_partial_calls_the_innermost_function(make_repo):
    g = graph_of(make_repo, "\ndef use(x):\n    p = partial(f, 1)\n    q = partial(p, 2)\n    return q()\n")
    assert callers_of(g, "m.f") == {"m.use"}


def test_a_partial_of_a_decorated_function_calls_what_the_decorator_returned(make_repo):
    body = """
    def deco(fn):
        def inner(*a):
            return fn(*a)
        return inner

    @deco
    def h(a, b):
        return a

    q = partial(h, 2)

    def use(x):
        return q(x)
    """
    g = graph_of(make_repo, body)
    assert callers_of(g, "m.deco.inner") == {"m.use"}
    assert callers_of(g, "m.h") == {"m.deco.inner"}


def test_a_partial_of_a_class_is_not_followed(make_repo):
    g = graph_of(make_repo, "\ndef use():\n    p = partial(K, 1)\n    return p(2)\n")
    assert callers_of(g, "m.K.__init__") == set()
    assert "not one function or method" in reasons(g, "m.use")[-1]


def test_a_name_assigned_twice_is_not_followed(make_repo):
    g = graph_of(make_repo, "\ndef use(x):\n    p = partial(f, 1)\n    p = partial(g, 1)\n    return p(x)\n")
    assert callers_of(g, "m.f") == set() and callers_of(g, "m.g") == set()
    assert "different functions" in reasons(g, "m.use")[0]


def test_the_same_partial_assigned_on_two_paths_is_followed(make_repo):
    body = "\ndef use(x, c):\n    if c:\n        p = partial(f, 1)\n    else:\n        p = partial(f, 2)\n    return p(x)\n"
    g = graph_of(make_repo, body)
    assert callers_of(g, "m.f") == {"m.use"}


def test_a_partial_the_name_is_also_reassigned_from_something_else_is_not_followed(make_repo):
    body = "\ndef use(x, other):\n    p = partial(f, 1)\n    p = other\n    return p(x)\n"
    g = graph_of(make_repo, body)
    assert callers_of(g, "m.f") == set()
    assert reasons(g, "m.use")


def test_a_parameter_or_loop_variable_of_the_same_name_is_not_followed(make_repo):
    body = "\ndef use(p, items):\n    for p in items:\n        p(1)\n\np = partial(f, 1)\n"
    g = graph_of(make_repo, body)
    assert callers_of(g, "m.f") == set()


def test_a_partial_that_is_not_functools_partial_is_not_followed(make_repo):
    body = """
    def partial(fn, *a):
        return lambda *b: 0

    def f(a, b):
        return a

    def use(x):
        p = partial(f, 1)
        return p(x)
    """
    g = build_graph(make_repo({"m.py": textwrap.dedent(body)}))
    assert callers_of(g, "m.f") == set()
    assert callers_of(g, "m.partial") == {"m.use"}


def test_a_partial_of_a_parameter_or_unknown_name_is_not_followed(make_repo):
    body = "\ndef make(fn):\n    p = partial(fn, 1)\n    return p(2)\n"
    g = graph_of(make_repo, body)
    assert "which ripple cannot resolve" in reasons(g, "m.make")[-1]
    assert callers_of(g, "m.f") == set()


def test_attributes_of_a_partial_are_not_calls_of_the_function(make_repo):
    g = graph_of(make_repo, "\ndef use(x):\n    p = partial(f, 1)\n    return p.func(x)\n")
    assert callers_of(g, "m.f") == set()


def test_a_module_partial_another_module_assigns_to_is_not_followed(make_repo):
    body = "\nT = partial(f, 1)\n\ndef use(x):\n    return T(x)\n"
    other = "import m\n\nm.T = None\n"
    g = graph_of(make_repo, body, **{"n.py": other})
    assert callers_of(g, "m.f") == set()
    assert "rebind" in reasons(g, "m.use")[0]


def test_a_module_partial_set_through_setattr_is_not_followed(make_repo):
    body = "\nT = partial(f, 1)\n\ndef use(x):\n    return T(x)\n"
    other = "import m\n\nsetattr(m, 'T', None)\n"
    g = graph_of(make_repo, body, **{"n.py": other})
    assert callers_of(g, "m.f") == set()


def test_a_module_that_uses_globals_or_a_star_import_is_not_followed(make_repo):
    body = "\nT = partial(f, 1)\n\ndef use(x):\n    return T(x)\n\ndef sneak():\n    globals()['T'] = 3\n"
    assert callers_of(graph_of(make_repo, body), "m.f") == set()
    star = "\nfrom outside import *\nT = partial(f, 1)\n\ndef use(x):\n    return T(x)\n"
    assert callers_of(graph_of(make_repo, star), "m.f") == set()


def test_a_module_partial_rebound_by_global_inside_a_function_is_not_followed(make_repo):
    body = "\nT = partial(f, 1)\n\ndef swap():\n    global T\n    T = partial(g, 1)\n\ndef use(x):\n    return T(x)\n"
    g = graph_of(make_repo, body)
    assert callers_of(g, "m.f") == set() and callers_of(g, "m.g") == set()


def test_a_module_partial_in_a_cycle_does_not_loop(make_repo):
    body = "\na = partial(b, 1)\nb = partial(a, 1)\n\ndef use():\n    return a()\n"
    g = graph_of(make_repo, body)
    assert callees_of(g, "m.use") == set()
