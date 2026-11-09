"""What the queries say about a symbol that is applied as a decorator.

The graphs are written by hand, so these run without tree-sitter. A decoration is not a call: the symbol's callers stay
what the edges prove, and each place it is written as a decorator is listed apart, with a note that what a decorator
does is not modelled. A Python graph has none of this, so its answers keep exactly the keys they always had.
"""

import json

from conftest import FIXTURES

from ripple.cli import format_result
from ripple.indexer import build_graph
from ripple.languages import merge_graphs
from ripple.model import Decoration, Edge, Graph, Symbol, Unresolved
from ripple.queries import callees, callers, impact, path


def sym(sid, kind, file, line=1):
    return Symbol(sid, kind, file, line, line + 1)


def decorated_graph():
    symbols = [
        sym("src/dec", "module", "src/dec.ts"),
        sym("src/dec:tag", "function", "src/dec.ts", 1),
        sym("src/dec:route", "function", "src/dec.ts", 2),
        sym("src/dec:unused", "function", "src/dec.ts", 3),
        sym("src/app", "module", "src/app.ts"),
        sym("src/app:run", "function", "src/app.ts", 20),
        sym("other/route", "module", "other/route.ts"),
        sym("other/route:route", "function", "other/route.ts", 1),
    ]
    edges = [Edge("src/app:run", "src/dec:route", "src/app.ts", 21)]
    unresolved = [
        Unresolved("src/app", "src/app.ts", 3, "@tag", "a decorator runs when the class is defined, and ripple does not follow what it does to the member"),
        Unresolved("src/app", "src/app.ts", 9, "@ns.route('/m')", "a decorator runs when the class is defined, and ripple does not follow what it does to the member"),
    ]
    decorations = [
        Decoration("src/app", "src/app.ts", 3, "@tag", "tag", "src/dec:tag"),
        Decoration("src/app", "src/app.ts", 7, "@tag", "tag", "src/dec:tag"),
        Decoration("src/app", "src/app.ts", 8, "@route('/x')", "route", "src/dec:route"),
        Decoration("src/app", "src/app.ts", 9, "@ns.route('/m')", "route", ""),  # a name that did not resolve
        Decoration("src/app", "src/app.ts", 12, "@other.route()", "route", "other/route:route"),
    ]
    modules = {s.file: s.id for s in symbols if s.kind == "module"}
    return Graph(symbols={s.id: s for s in symbols}, edges=edges, unresolved=unresolved, decorations=decorations, modules=modules)


def test_a_symbol_only_ever_used_as_a_decorator_has_no_callers_and_lists_where_it_is_applied():
    result = callers(decorated_graph(), "src/dec:tag")
    assert result["count"] == 0 and result["callers"] == []  # a decoration is not a call edge
    assert result["decorations"] == [
        {"in": "src/app", "applied_at": "src/app.ts:3", "written": "@tag"},
        {"in": "src/app", "applied_at": "src/app.ts:7", "written": "@tag"},
    ]
    assert "not calls" in result["decoration_note"] and "not modelled" in result["decoration_note"]
    assert "possible_decorations" not in result


def test_a_decorator_also_called_directly_keeps_that_call_exact_and_lists_its_decorations_apart():
    result = callers(decorated_graph(), "src/dec:route")
    assert [c["symbol"] for c in result["callers"]] == ["src/app:run"]
    assert result["callers"][0]["call_sites"] == ["src/app.ts:21"]
    assert [d["applied_at"] for d in result["decorations"]] == ["src/app.ts:8"]
    # the same name whose resolution failed is listed as a possibility, never as proven
    assert [d["applied_at"] for d in result["possible_decorations"]] == ["src/app.ts:9"]
    assert "src/app.ts:12" not in json.dumps(result)  # resolved to a different `route`


def test_impact_lists_the_decorations_and_does_not_count_them_as_affected():
    result = impact(decorated_graph(), "src/dec:tag")
    assert result["affected"] == 0 and result["caller_count"] == 0
    assert [d["applied_at"] for d in result["decorations"]] == ["src/app.ts:3", "src/app.ts:7"]
    assert "decoration_note" in result
    again = impact(decorated_graph(), "src/dec:route")
    assert again["affected"] == 1
    assert [d["applied_at"] for d in again["decorations"]] == ["src/app.ts:8"]
    assert [d["applied_at"] for d in again["possible_decorations"]] == ["src/app.ts:9"]


def test_path_to_a_decorator_says_it_is_applied_even_when_no_chain_reaches_it():
    result = path(decorated_graph(), "src/dec:unused", "src/dec:tag")
    assert result["path"] is None
    assert [d["applied_at"] for d in result["decorations"]] == ["src/app.ts:3", "src/app.ts:7"]


def test_callees_of_the_scope_that_applies_decorators_names_each_one_and_whether_it_resolved():
    result = callees(decorated_graph(), "src/app")
    by_line = {d["applied_at"]: d for d in result["applies_decorators"]}
    assert by_line["src/app.ts:3"] == {"decorator": "src/dec:tag", "applied_at": "src/app.ts:3", "written": "@tag"}
    assert by_line["src/app.ts:9"]["decorator"] is None  # not proven, so no id is given
    assert result["callees"] == []  # nothing here is a call edge


def test_a_symbol_never_used_as_a_decorator_answers_exactly_as_before():
    graph = decorated_graph()
    for result in (callers(graph, "src/dec:unused"), impact(graph, "src/dec:unused"), callees(graph, "src/dec:unused")):
        assert not {"decorations", "possible_decorations", "decoration_note", "applies_decorators"} & set(result)


def test_a_module_is_never_matched_by_the_name_of_a_decorator():
    graph = decorated_graph()
    assert "possible_decorations" not in impact(graph, "other/route")


def test_the_text_output_says_what_it_is_listing():
    graph = decorated_graph()
    text = format_result("callers", callers(graph, "src/dec:route"))
    assert "callers of src/dec:route (1):" in text
    assert "applied as a decorator (1), not calls; what a decorator does is not modelled:" in text
    assert "  src/app  src/app.ts:8  written @route('/x')" in text
    assert "applied as a decorator under a name ripple could not resolve (1), check by hand:" in text
    text = format_result("impact", impact(graph, "src/dec:tag"))
    assert "applied as a decorator (2)" in text and "src/app.ts:3" in text


def test_a_graph_with_decorations_round_trips_through_the_cache_form_and_one_without_has_no_key():
    graph = decorated_graph()
    again = Graph.from_dict(json.loads(json.dumps(graph.to_dict())))
    assert again.decorations == graph.decorations
    assert "decorations" not in Graph().to_dict()  # a Python graph is written exactly as it was


def test_python_answers_keep_exactly_the_keys_they_had():
    graph = build_graph(FIXTURES / "shop")
    assert graph.decorations == []
    assert "decorations" not in graph.to_dict()
    result = callers(graph, "shop.tax.tax_for")
    assert set(result) == {"symbol", "count", "callers", "through_wrapper"}
    assert set(impact(graph, "shop.tax.tax_for")) == {
        "symbol", "depth", "affected", "more_beyond_depth", "by_file", "caller_count", "tests", "test_count",
        "truncated", "omitted_callers", "omitted_tests", "possible_missed_callers", "possible_missed_total",
    }
    assert set(callees(graph, "shop.tax.tax_for")) == {"symbol", "callees", "unresolved"}


def test_a_question_about_renders_only_lists_no_decorations():
    graph = decorated_graph()
    for result in (callers(graph, "src/dec:tag", kinds=frozenset({"renders"})), impact(graph, "src/dec:tag", kinds=frozenset({"renderer"}))):
        assert "decorations" not in result
    assert "decorations" in callers(graph, "src/dec:tag", kinds=frozenset({"calls"}))


def test_a_long_list_is_cut_at_the_limit_and_says_how_many_were_left_out():
    graph = decorated_graph()
    result = impact(graph, "src/dec:tag", limit=1)
    assert len(result["decorations"]) == 1 and result["decorations_omitted"] == 1
    assert "decorations_omitted" not in impact(graph, "src/dec:tag", limit=2)
    assert "1 more decorator sites not listed" in format_result("impact", result)


def test_impact_is_truncated_when_decoration_sites_were_left_out_and_not_otherwise():
    graph = decorated_graph()
    cut = impact(graph, "src/dec:tag", limit=1)
    assert cut["omitted_callers"] == 0 and cut["omitted_tests"] == 0
    assert cut["decorations_omitted"] == 1 and cut["truncated"] is True
    whole = impact(graph, "src/dec:tag", limit=2)
    assert "decorations_omitted" not in whole and whole["truncated"] is False


def test_decorations_survive_a_merge_of_two_languages_and_a_dropped_id_takes_its_decorations_with_it():
    ts = decorated_graph()
    py = Graph(symbols={"app": sym("app", "module", "app.py")}, modules={"app.py": "app"})
    merged = merge_graphs([py, ts])
    assert merged.decorations == ts.decorations
    clash = Graph(symbols={"src/app": sym("src/app", "module", "src/app.py")}, modules={"src/app.py": "src/app"})
    again = merge_graphs([clash, ts])  # the TypeScript module `src/app` is dropped: its decorations are too
    assert again.decorations == [d for d in ts.decorations if d.holder != "src/app"]
