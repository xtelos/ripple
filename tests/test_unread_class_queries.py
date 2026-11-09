"""What the queries say about a symbol that a class ripple does not read may call.

The graphs are written by hand, so these run without tree-sitter. The unresolved call inside such a class carries the
UNREAD_CLASS reason; callers (which lists no unresolved calls of its own) lists exactly those that end with the symbol's
name, and impact says which of its same-name sites are of that kind.
"""

from ripple.model import UNREAD_CLASS, Edge, Graph, Symbol, Unresolved
from ripple.queries import callers, impact


def sym(sid, kind, file, line=1):
    return Symbol(sid, kind, file, line, line + 1)


def graph():
    symbols = [
        sym("src/m", "module", "src/m.ts"),
        sym("src/m:transform", "function", "src/m.ts", 1),
        sym("src/m:make", "function", "src/m.ts", 3),
        sym("src/m:other", "function", "src/m.ts", 9),
        sym("src/transform", "module", "src/transform.ts"),
    ]
    unresolved = [
        Unresolved("src/m:make", "src/m.ts", 5, "this.transform", UNREAD_CLASS),
        Unresolved("src/m:other", "src/m.ts", 10, "x.transform", "the receiver is an unannotated parameter"),
    ]
    modules = {s.file: s.id for s in symbols if s.kind == "module"}
    return Graph(symbols={s.id: s for s in symbols}, edges=[Edge("src/m:other", "src/m:transform", "src/m.ts", 11)], unresolved=unresolved, modules=modules)


def test_callers_lists_only_the_calls_in_unread_classes_and_says_why():
    result = callers(graph(), "src/m:transform")
    assert [c["symbol"] for c in result["callers"]] == ["src/m:other"]
    assert result["possible_missed_callers"] == [
        {"caller": "src/m:make", "call": "this.transform", "location": "src/m.ts:5", "reason": UNREAD_CLASS}
    ]  # `x.transform` is another kind of unresolved call, which impact lists and callers never has
    assert result["possible_missed_total"] == 1


def test_impact_marks_which_same_name_sites_are_inside_unread_classes():
    result = impact(graph(), "src/m:transform")
    by_call = {m["call"]: m for m in result["possible_missed_callers"]}
    assert by_call["this.transform"]["reason"] == UNREAD_CLASS
    assert "reason" not in by_call["x.transform"]  # an ordinary same-name site keeps the shape it had


def test_a_module_is_not_matched_by_name_and_a_symbol_no_unread_class_calls_gets_no_key():
    assert "possible_missed_callers" not in callers(graph(), "src/transform")
    assert "possible_missed_callers" not in callers(graph(), "src/m:make")


def test_a_question_about_renders_only_lists_none():
    assert "possible_missed_callers" not in callers(graph(), "src/m:transform", kinds=frozenset({"renders"}))
