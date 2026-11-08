"""The kind of an edge in what a query says: `calls`, `renders` and `renderer`, and the `--kinds` filter.

The graphs are written by hand, so these run without tree-sitter. A graph with no JSX edge must answer exactly as it
always did (no label), which is also what keeps Python output unchanged.
"""

import json

import pytest

from ripple import cli, queries
from ripple.jsx_facts import JSX_NO_RENDERER, JSX_TAG_UNRESOLVED
from ripple.model import Edge, Graph, Symbol, Unresolved


def sym(sid, kind="function", file="src/m.tsx", line=1):
    return Symbol(sid, kind, file, line, line + 1)


def graph():
    ids = ["util", "Leaf", "Row", "Page"]
    return Graph(
        symbols={"src/m": sym("src/m", "module"), **{f"src/m:{i}": sym(f"src/m:{i}", line=n) for n, i in enumerate(ids, 2)}},
        edges=[
            Edge("src/m:Row", "src/m:util", "src/m.tsx", 4),
            Edge("src/m:Row", "src/m:Leaf", "src/m.tsx", 5, "renders"),
            Edge("src/m:Row", "src/m:Leaf", "src/m.tsx", 5, "renderer"),
            Edge("src/m:Page", "src/m:Row", "src/m.tsx", 7, "renders"),
        ],
        unresolved=[
            Unresolved("src/m:Page", "src/m.tsx", 7, "Row", JSX_NO_RENDERER + "nothing renders it"),
            Unresolved("src/m:Page", "src/m.tsx", 8, "Comp", JSX_TAG_UNRESOLVED + "a variable"),
            Unresolved("src/m:Page", "src/m.tsx", 9, "util", "a computed call"),
        ],
    )


def test_parse_kinds():
    assert queries.parse_kinds(None) is None and queries.parse_kinds("all") is None and queries.parse_kinds("") is None
    assert queries.parse_kinds("renders, Calls") == {"renders", "calls"}
    assert queries.parse_kinds("calls,all") is None
    with pytest.raises(ValueError, match="unknown edge kind 'render'"):
        queries.parse_kinds("render")


def test_callers_label_a_jsx_edge_with_its_kinds_and_sites_and_leave_a_call_unlabelled():
    found = queries.callers(graph(), "src/m:Leaf")["callers"]
    assert found == [
        {
            "symbol": "src/m:Row",
            "kind": "function",
            "location": "src/m.tsx:4",
            "call_sites": ["src/m.tsx:5"],
            "edge_kinds": ["renders", "renderer"],
            "sites_by_kind": {"renders": ["src/m.tsx:5"], "renderer": ["src/m.tsx:5"]},
        }
    ]
    assert queries.callers(graph(), "src/m:util")["callers"] == [
        {"symbol": "src/m:Row", "kind": "function", "location": "src/m.tsx:4", "call_sites": ["src/m.tsx:4"]}
    ]


def test_kinds_select_which_edges_a_question_follows():
    g = graph()
    assert [e["edge_kinds"] for e in queries.callers(g, "src/m:Leaf", kinds={"renders"})["callers"]] == [["renders"]]
    assert queries.callers(g, "src/m:Leaf", kinds={"calls"})["callers"] == []
    assert queries.impact(g, "src/m:Leaf", kinds={"calls"})["affected"] == 0
    assert queries.impact(g, "src/m:Leaf", kinds={"renders"})["affected"] == 2
    assert queries.impact(g, "src/m:Leaf")["affected"] == 2
    assert queries.path(g, "src/m:Page", "src/m:Leaf", kinds={"calls"})["path"] is None
    assert [h["symbol"] for h in queries.path(g, "src/m:Page", "src/m:Leaf", kinds={"renderer"})["path"] or []] == []


def test_impact_says_which_kinds_reached_each_caller():
    result = queries.impact(graph(), "src/m:Leaf")
    entries = [e for entries in result["by_file"].values() for e in entries]
    assert [(e["symbol"], e["distance"], e["edge_kinds"]) for e in entries] == [
        ("src/m:Row", 1, ["renders", "renderer"]),
        ("src/m:Page", 2, ["renders"]),
    ]
    # a caller reached only by a call is as it always was
    plain = [e for entries in queries.impact(graph(), "src/m:util")["by_file"].values() for e in entries]
    assert [(e["symbol"], e.get("edge_kinds")) for e in plain] == [("src/m:Row", None), ("src/m:Page", ["renders"])]


def test_callees_and_path_label_the_kind_and_the_unresolved_list_follows_the_filter():
    g = graph()
    row = queries.callees(g, "src/m:Row")
    assert {e["symbol"]: e.get("edge_kinds") for e in row["callees"]} == {"src/m:Leaf": ["renders", "renderer"], "src/m:util": None}
    page = queries.callees(g, "src/m:Page")
    assert [u["call"] for u in page["unresolved"]] == ["Row", "Comp", "util"]
    assert [u["call"] for u in queries.callees(g, "src/m:Page", kinds={"renderer"})["unresolved"]] == ["Row"]
    assert [u["call"] for u in queries.callees(g, "src/m:Page", kinds={"renders"})["unresolved"]] == ["Comp"]
    assert [u["call"] for u in queries.callees(g, "src/m:Page", kinds={"calls"})["unresolved"]] == ["util"]
    hops = queries.path(g, "src/m:Page", "src/m:Leaf")["path"]
    assert [h.get("edge_kinds") for h in hops] == [None, ["renders"], ["renders", "renderer"]]


def test_an_unproven_renderer_is_not_listed_as_a_possible_missed_caller():
    # it names the symbol, and its caller is the creator, who is already a caller: it would only be noise
    missed = queries.impact(graph(), "src/m:Row")["possible_missed_callers"]
    assert missed == []
    assert [m["call"] for m in queries.impact(graph(), "src/m:util")["possible_missed_callers"]] == ["util"]


def test_the_text_output_names_the_kind_of_each_site(capsys):
    g = graph()
    assert cli.format_result("callers", queries.callers(g, "src/m:Leaf")) == (
        "callers of src/m:Leaf (1):\n  src/m:Row  src/m.tsx:4  renders at src/m.tsx:5; renderer at src/m.tsx:5"
    )
    assert cli.format_result("callers", queries.callers(g, "src/m:util")) == (
        "callers of src/m:util (1):\n  src/m:Row  src/m.tsx:4  called at src/m.tsx:4"
    )
    assert "via renders, renderer" in cli.format_result("impact", queries.impact(g, "src/m:Leaf"))
    assert "(renders at src/m.tsx:7)" in cli.format_result("path", queries.path(g, "src/m:Page", "src/m:Leaf"))


def test_the_cli_takes_kinds_and_refuses_a_word_that_is_none():
    parser = cli.build_parser()
    assert parser.parse_args(["callers", "x"]).kinds is None
    assert parser.parse_args(["impact", "x", "--kinds", "calls,renders"]).kinds == {"calls", "renders"}
    assert parser.parse_args(["path", "a", "b", "--kinds", "all"]).kinds is None
    with pytest.raises(SystemExit):
        parser.parse_args(["callees", "x", "--kinds", "render"])


def test_a_callee_lists_a_site_once_for_each_call_written_there_and_a_caller_lists_it_once():
    # as callers and callees always did: two calls on one line are two entries of the callee's sites
    g = Graph(
        symbols={"m:a": sym("m:a"), "m:b": sym("m:b")},
        edges=[Edge("m:a", "m:b", "m.py", 3), Edge("m:a", "m:b", "m.py", 3)],
    )
    assert queries.callees(g, "m:a")["callees"][0]["call_sites"] == ["m.py:3", "m.py:3"]
    assert queries.callers(g, "m:b")["callers"][0]["call_sites"] == ["m.py:3"]


def test_the_results_are_plain_json():
    json.dumps(queries.impact(graph(), "src/m:Leaf"))
    json.dumps(queries.callers(graph(), "src/m:Leaf"))
