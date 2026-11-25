"""A member that is only a setter is not what a call of its name runs, in Python as in JavaScript.

Python: `@x.setter def y(...)` under another name makes `y` a property whose getter is `x`'s, so `self.y()` calls what that getter gives.
JavaScript: `set m(v) {}` alone leaves `m` with no getter, so `this.m()` throws. Either way the setter's body is not what the call runs, and
the answer is no edge to it. The reason ripple gives is the one that is true in both: a setter is not a call target, the call runs what a
getter gives (and throws when there is none).

The Python cases run the code, so the claims about what Python does are Python's, not ours.
"""

import textwrap

import pytest

from ripple.indexer import build_graph

PY_SETTER_UNDER_ANOTHER_NAME = """\
    class Other:
        def run(self):
            return "RUN"


    class C:
        @property
        def x(self):
            return Other

        @x.setter
        def y(self, v):
            self._v = v

        def go(self):
            return self.y()  # CALL


    RESULT = C().go().run()
    """


def run(source):
    namespace: dict = {}
    exec(compile(textwrap.dedent(source), "m.py", "exec"), namespace)
    return namespace["RESULT"]


def answer(graph, source, marker="# CALL", file="m.py"):
    """(the sorted (callee, kind) edges, the reasons) of the call on the line of `source` that carries `marker`."""
    line = next(i for i, text in enumerate(textwrap.dedent(source).splitlines(), 1) if marker in text)
    edges = sorted((e.callee, e.kind) for e in graph.edges if e.file == file and e.line == line)
    reasons = [u.reason for u in graph.unresolved if u.file == file and u.line == line]
    return edges, reasons


def test_python_runs_the_getters_result_for_a_setter_declared_under_another_name():
    # the claim the cases below rest on: the call runs what x's getter gave (Other), and the setter body never ran
    assert run(PY_SETTER_UNDER_ANOTHER_NAME) == "RUN"


def test_a_python_setter_declared_under_another_name_is_no_call_edge_and_its_reason_is_true_there(make_repo):
    graph = build_graph(make_repo({"m.py": PY_SETTER_UNDER_ANOTHER_NAME}))
    edges, reasons = answer(graph, PY_SETTER_UNDER_ANOTHER_NAME)
    assert not [e for e in edges if e[0] == "m.C.y"], edges
    assert len(reasons) == 1, reasons
    # Python has a getter here (x's), and nothing gives `undefined`: the JavaScript wording of the old reason was false in Python
    assert "setter" in reasons[0]
    assert "undefined" not in reasons[0] and "no getter" not in reasons[0], reasons[0]
    assert "what a getter gives" in reasons[0], reasons[0]


def test_a_python_method_and_a_property_with_a_setter_keep_their_answers(make_repo):
    source = """\
        class Other:
            def run(self):
                return "RUN"


        class C:
            def y(self):
                return Other()

            @property
            def z(self):
                return Other

            @z.setter
            def z(self, v):
                self._v = v

            def go(self):
                self.y()  # METHOD
                return self.z()  # PROPERTY
        """
    graph = build_graph(make_repo({"m.py": source}))
    assert answer(graph, source, "# METHOD")[0] == [("m.C.y", "call")]
    edges, reasons = answer(graph, source, "# PROPERTY")
    assert not [e for e in edges if e[0] == "m.C.z"], edges  # the call is of what the getter gave, as before this change
    assert not any("setter" in r for r in reasons), reasons


def test_a_python_subclass_member_that_is_only_a_setter_is_no_override_edge(make_repo):
    # `D.y` is a property whose getter is `x`'s, so `D().go()` calls what that getter gave and not the setter's body. The base's own
    # method is still what `self.y()` runs on a B.
    source = """\
        class Other:
            def run(self):
                return "RUN"


        class B:
            def y(self):
                return "B"

            def go(self):
                return self.y()  # CALL


        class D(B):
            @property
            def x(self):
                return Other

            @x.setter
            def y(self, v):
                self._v = v


        RESULT = [B().go(), D().go().run()]
        """
    assert run(source) == ["B", "RUN"]
    graph = build_graph(make_repo({"m.py": source}))
    assert answer(graph, source)[0] == [("m.B.y", "call")]


def test_a_python_subclass_override_that_is_a_method_stays_an_override_edge(make_repo):
    source = """\
        class B:
            def y(self):
                return "B"

            def go(self):
                return self.y()  # CALL


        class D(B):
            def y(self):
                return "D"
        """
    graph = build_graph(make_repo({"m.py": source}))
    assert answer(graph, source)[0] == [("m.B.y", "call"), ("m.D.y", "override")]


def test_a_python_subclass_getter_and_setter_of_the_name_is_not_a_setter_alone(make_repo):
    # `@property def y` then `@y.setter def y`: one property with a getter. Its def of the name is a call target as it was
    source = """\
        class B:
            def y(self):
                return "B"

            def go(self):
                return self.y()  # CALL


        class D(B):
            @property
            def y(self):
                return lambda: "D"

            @y.setter
            def y(self, v):
                self._v = v
        """
    graph = build_graph(make_repo({"m.py": source}))
    assert ("m.D.y", "override") in answer(graph, source)[0]


@pytest.mark.parametrize("member", ["set m(v) { this._v = v; }", "m() { return 'M'; } set m(v) { this._v = v; }"])
def test_the_javascript_reason_is_the_same_one(make_repo, member):
    pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
    pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
    from ripple.profile import JAVASCRIPT

    main = "import { C } from './c.js';\nconsole.log(new C().m()); // CALL\n"
    graph = build_graph(make_repo({"c.js": f"export class C {{ {member} }}\n", "main.js": main}), JAVASCRIPT)
    reasons = [u.reason for u in graph.unresolved if u.file == "main.js" and u.line == 2]
    assert len(reasons) == 1 and "what a getter gives" in reasons[0] and "setter" in reasons[0], reasons
