"""`global NAME` followed by a def, class, import or except-as in a function rebinds the module's NAME.

A module-level partial or table entry that code does this to is not the one the call finds.
"""

import pytest

from ripple.indexer import build_graph

HEAD = """\
from functools import partial

def default_handler(prefix, msg):
    return prefix + msg

def loud_handler(msg):
    return msg.upper()

handler = partial(default_handler, "> ")
"""

USE = """
def emit(msg):
    return handler(msg)
"""


def callees(graph, caller):
    return {e.callee for e in graph.edges if e.caller == caller}


def test_the_partial_alone_is_proven(make_repo):
    g = build_graph(make_repo({"svc.py": HEAD + USE}))
    assert callees(g, "svc.emit") == {"svc.default_handler"}


@pytest.mark.parametrize(
    "rebind",
    [
        "def configure():\n    global handler\n    from other import loud_handler as handler\n",
        "def configure():\n    global handler\n    import other as handler\n",
        "def configure():\n    global handler\n\n    def handler(msg):\n        return loud_handler(msg)\n",
        "def configure():\n    global handler\n\n    class handler:\n        pass\n",
        "def configure():\n    global handler\n    try:\n        pass\n    except ValueError as handler:\n        pass\n",
        "def configure():\n    global handler\n    match 1:\n        case 1 as handler:\n            pass\n",
        "def configure():\n    global handler\n    with open('x') as handler:\n        pass\n",
        "def configure():\n    global handler\n    for handler in []:\n        pass\n",
    ],
)
def test_a_global_rebind_in_a_function_leaves_the_partial_unresolved(make_repo, rebind):
    g = build_graph(make_repo({"svc.py": HEAD + rebind + USE, "other.py": "def loud_handler(msg):\n    return msg\n"}))
    assert callees(g, "svc.emit") == set()
    assert [u.text for u in g.unresolved if u.caller == "svc.emit"] == ["handler"]


def test_a_global_rebind_in_a_nested_function_counts_too(make_repo):
    rebind = (
        "def outer():\n    def inner():\n        global handler\n        from other import loud_handler as handler\n"
        "    inner()\n"
    )
    g = build_graph(make_repo({"svc.py": HEAD + rebind + USE, "other.py": "def loud_handler(msg):\n    return msg\n"}))
    assert callees(g, "svc.emit") == set()


def test_a_function_that_names_its_own_local_handler_is_not_a_rebind(make_repo):
    local = "def configure():\n    from other import loud_handler as handler\n    return handler('x')\n"
    g = build_graph(make_repo({"svc.py": HEAD + local + USE, "other.py": "def loud_handler(msg):\n    return msg\n"}))
    assert callees(g, "svc.emit") == {"svc.default_handler"}


def test_a_global_def_makes_the_function_the_modules_binding_for_calls(make_repo):
    # With no other binding, `global f; def f(): ...` defines the module's f, and a call of f finds it.
    src = "def install():\n    global late\n\n    def late():\n        pass\n\ndef use():\n    late()\n"
    g = build_graph(make_repo({"m.py": src}))
    assert callees(g, "m.use") == {"m.install.late"}
