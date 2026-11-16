"""What a user sees of a Go `implements` edge: callers, callees, impact, the statistics, the CLI text and the MCP server.

An `implements` edge is a Go call through an interface that MAY dispatch to a repo method. It is never a call, so none of
the places that count or follow calls may see one: not `callers`'s list or count, not `callees`, not impact's `affected`,
`caller_count` and affected tests, not the `edges` statistic, not a `--kinds calls` question, not a path. It is listed
apart, under its own keys, worded as "may" and with a note that the list is never complete.

The first half is written by hand (no tree-sitter), the second runs the real thing on the bench/fixtures/go probe
`impl_basic` (the Go front end and the CLI and the MCP server on top of it).

Every reader of an edge's kind in the repository, and what each does with `implements` (checked by hand and pinned by the
tests below where a test can see it):
  ripple/model.py     Graph._index keeps it out of callers_index, callees_index and via_index (it has implementers_index
                      and dispatch_index of its own); Graph.stats leaves it out of `edges` and counts `implements_edges`;
                      to_dict and from_dict carry it like any edge.
  ripple/queries.py   edge_group names it `implements`; callers, callees, impact and trace read only the call indexes, so
                      they never follow it, and list it through the three _implementers/_dispatch functions, which obey
                      `kinds`. KINDS names it, so `kinds=implements` is valid and `kinds=calls` drops it.
  ripple/cli.py       format_index counts JSX edges by kind (an implements edge is not one) and prints
                      `implements_edges` from the statistics; format_interface_calls prints the three keys.
  ripple/server.py    returns the queries' dicts as they are and documents the keys.
  ripple/languages.py merge_graphs keeps an edge unless it names a dropped (ambiguous) id as caller, callee or via, so an
                      implements edge to an id two languages define is dropped with it (its via is the interface).
  bench/ts_score.py   scores TypeScript graphs only, by an explicit set of kinds; never sees a Go edge.
  bench/ts_tracer     TypeScript runtime edges, no Go.
  ripple/go_indexer.py, java_resolver.py, resolver.py, ts_indexer.py, jsx.py, rs_indexer.py write edges; only go_indexer
                      writes this kind.
"""

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES
from stdio_client import StdioClient

from ripple import cli, queries
from ripple.cache import load_repo
from ripple.languages import RepoGraph
from ripple.model import IMPLEMENTS, Edge, Graph, Symbol

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
PROBE = Path(__file__).resolve().parent.parent / "bench" / "fixtures" / "go" / "probes" / "impl_basic"
PKG = "example.com/impl_basic"
KEYS = ("may_be_called_through_interface", "may_be_reached_through_interface", "may_dispatch_to")


def sym(sid, file, line, kind="function"):
    return Symbol(sid, kind, file, line, line + 2)


def graph():
    return Graph(
        symbols={
            "app:Main": sym("app:Main", "app.go", 1),
            "app:Use": sym("app:Use", "app.go", 5),
            "app:T.Run": sym("app:T.Run", "app.go", 10, "method"),
            "app:U.Run": sym("app:U.Run", "app.go", 14, "method"),
            "app:TestT": sym("app:TestT", "app_test.go", 3),
        },
        edges=[
            Edge("app:Main", "app:Use", "app.go", 2),
            Edge("app:TestT", "app:T.Run", "app_test.go", 4),
            Edge("app:Use", "app:T.Run", "app.go", 6, IMPLEMENTS, "app:Runner"),
            Edge("app:Use", "app:U.Run", "app.go", 6, IMPLEMENTS, "app:Runner"),
            Edge("app:Use", "app:T.Run", "app.go", 7, IMPLEMENTS, "app:Runner"),
        ],
    )


def test_callers_list_the_possible_callers_apart_and_count_only_the_calls():
    result = queries.callers(graph(), "app:T.Run")
    assert result["count"] == 1 and [c["symbol"] for c in result["callers"]] == ["app:TestT"]
    (listed,) = result["may_be_called_through_interface"]
    assert listed["symbol"] == "app:Use" and listed["interface"] == "app:Runner" and listed["call_sites"] == ["app.go:6", "app.go:7"]
    assert "never complete" in result["may_be_called_note"] and "MAY" in result["may_be_called_note"]
    assert "may_dispatch_to" not in result and "may_be_reached_through_interface" not in result
    assert "app:Use" not in {c["symbol"] for c in result["callers"]}
    # a symbol that only an interface call may reach has no caller at all, and says so
    only = queries.callers(graph(), "app:U.Run")
    assert only["count"] == 0 and only["callers"] == [] and len(only["may_be_called_through_interface"]) == 1
    # a symbol no interface call reaches answers with the keys it always had
    assert not set(KEYS) & set(queries.callers(graph(), "app:Use"))


def test_callees_list_what_the_interface_calls_may_dispatch_to_apart_from_the_calls():
    result = queries.callees(graph(), "app:Use")
    assert result["callees"] == []
    assert [(e["symbol"], e["call_sites"]) for e in result["may_dispatch_to"]] == [("app:T.Run", ["app.go:6", "app.go:7"]), ("app:U.Run", ["app.go:6"])]
    assert all(e["interface"] == "app:Runner" and e["kind"] == "method" for e in result["may_dispatch_to"])
    assert "never calls" in result["may_dispatch_note"] and "never complete" in result["may_dispatch_note"]
    main = queries.callees(graph(), "app:Main")
    assert [c["symbol"] for c in main["callees"]] == ["app:Use"] and not set(KEYS) & set(main)


def test_impact_counts_calls_only_and_lists_the_interface_calls_that_may_reach_it():
    result = queries.impact(graph(), "app:T.Run")
    assert result["affected"] == 1 and result["caller_count"] == 1
    assert [e["symbol"] for entries in result["by_file"].values() for e in entries] == ["app:TestT"]
    (row,) = result["may_be_reached_through_interface"]
    assert row == {"caller": "app:Use", "interface": "app:Runner", "may_reach": "app:T.Run", "distance": 0, "call_sites": ["app.go:6", "app.go:7"]}
    assert "possible callers, not callers" in result["may_be_reached_note"] and "never complete" in result["may_be_reached_note"]
    assert "does not continue past" in result["may_be_reached_note"]
    # Use is only a possible caller, so what calls Use (Main) is not affected: the reach is not followed past it
    assert "app:Main" not in json.dumps(result["by_file"]) and "app:Use" not in json.dumps(result["by_file"])
    nothing = queries.impact(graph(), "app:U.Run")
    assert nothing["affected"] == 0 and nothing["caller_count"] == 0 and len(nothing["may_be_reached_through_interface"]) == 1
    # a caller impact found by a call is itself reachable by an interface call: that row says which symbol it may reach
    deeper = dataclasses.replace(graph(), edges=graph().edges + [Edge("app:Main", "app:TestT", "app.go", 3, IMPLEMENTS, "app:Tester")])
    rows = queries.impact(deeper, "app:T.Run")["may_be_reached_through_interface"]
    assert {(r["caller"], r["may_reach"], r["distance"]) for r in rows} == {("app:Use", "app:T.Run", 0), ("app:Main", "app:TestT", 1)}
    assert queries.impact(deeper, "app:T.Run")["affected"] == 1


def test_a_kinds_filter_for_calls_drops_them_and_one_for_implements_keeps_only_them():
    calls = frozenset({"calls"})
    only = frozenset({"implements"})
    for result in (queries.callers(graph(), "app:T.Run", kinds=calls), queries.impact(graph(), "app:T.Run", kinds=calls), queries.callees(graph(), "app:Use", kinds=calls)):
        assert not set(KEYS) & set(result), result
    assert queries.callers(graph(), "app:T.Run", kinds=calls)["count"] == 1
    assert queries.impact(graph(), "app:T.Run", kinds=calls)["affected"] == 1
    kept = queries.callers(graph(), "app:T.Run", kinds=only)
    assert kept["count"] == 0 and kept["callers"] == [] and kept["may_be_called_through_interface"]
    assert queries.impact(graph(), "app:T.Run", kinds=only)["affected"] == 0
    assert queries.impact(graph(), "app:T.Run", kinds=only)["may_be_reached_through_interface"]
    assert queries.callees(graph(), "app:Use", kinds=only)["may_dispatch_to"]
    assert queries.parse_kinds("implements") == {"implements"} and "implements" in queries.KINDS
    with pytest.raises(ValueError, match="unknown edge kind"):
        queries.parse_kinds("implement")


def test_a_path_never_crosses_an_implements_edge():
    assert queries.path(graph(), "app:Main", "app:T.Run")["path"] is None
    assert queries.path(graph(), "app:Use", "app:T.Run")["path"] is None
    assert queries.path(graph(), "app:TestT", "app:T.Run")["path"] is not None  # a call is


def test_the_statistics_do_not_count_them_as_calls_and_a_graph_without_any_has_no_key():
    stats = graph().stats()
    assert stats["edges"] == 2 and stats["implements_edges"] == 3
    plain = dataclasses.replace(graph(), edges=[e for e in graph().edges if e.kind != IMPLEMENTS])
    assert plain.stats()["edges"] == 2 and "implements_edges" not in plain.stats()


def test_the_graph_round_trips_through_its_dict_and_keeps_the_edges_apart():
    again = Graph.from_dict(graph().to_dict())
    assert again.edges == graph().edges
    assert sorted(again.implementers_index) == ["app:T.Run", "app:U.Run"] and sorted(again.dispatch_index) == ["app:Use"]
    assert "app:Use" not in again.callers_index.get("app:T.Run", []) and [e.caller for e in again.callers_index["app:T.Run"]] == ["app:TestT"]
    assert all(e.kind != IMPLEMENTS for edges in again.callers_index.values() for e in edges)
    assert all(e.kind != IMPLEMENTS for edges in again.callees_index.values() for e in edges)
    assert all(e.kind != IMPLEMENTS for edges in again.via_index.values() for e in edges)


def test_what_a_test_reaches_only_through_a_possible_caller_is_not_an_affected_test(tmp_path):
    """The Python shop's tests: best_discount is called by three tests and is not affected by tax_for. An implements edge from it to
    tax_for lists it as a possible caller and adds none of its tests to tax_for's affected tests or to `affected`."""
    shop = load_repo(str(FIXTURES / "shop"), cache_dir=str(tmp_path)).graph
    plain = queries.impact(shop, "tax_for")
    assert plain["affected"] == 6 and plain["test_count"] == 3 and "shop.discounts.best_discount" not in json.dumps(plain["by_file"])
    assert queries.impact(shop, "best_discount")["test_count"] == 3  # the tests it has, which are not tax_for's
    extra = Edge("shop.discounts.best_discount", "shop.tax.tax_for", "shop/discounts.py", 10, IMPLEMENTS, "shop.iface:Taxer")
    with_edge = queries.impact(dataclasses.replace(shop, edges=[*shop.edges, extra]), "tax_for")
    assert [r["caller"] for r in with_edge["may_be_reached_through_interface"]] == ["shop.discounts.best_discount"]
    for key in ("affected", "caller_count", "test_count", "tests", "by_file"):
        assert with_edge[key] == plain[key], key


def test_the_listed_rows_are_cut_at_the_limit_and_truncated_says_so():
    many = graph()
    edges = list(many.edges) + [Edge(f"app:C{i}", "app:T.Run", "app.go", 20 + i, IMPLEMENTS, "app:Runner") for i in range(5)]
    symbols = {**many.symbols, **{f"app:C{i}": sym(f"app:C{i}", "app.go", 20 + i) for i in range(5)}}
    big = Graph(symbols=symbols, edges=edges)
    result = queries.impact(big, "app:T.Run", limit=2)
    assert len(result["may_be_reached_through_interface"]) == 2 and result["may_be_reached_omitted"] == 4 and result["truncated"] is True
    assert queries.callers(big, "app:T.Run")["may_be_called_through_interface"][0]["symbol"] == "app:C0"
    assert "may_be_called_omitted" not in queries.callers(big, "app:T.Run")


def test_the_cli_text_names_the_three_lists_as_possible_and_never_complete():
    callers = cli.format_answer("callers", queries.callers(graph(), "app:T.Run"))
    assert "callers of app:T.Run (1):" in callers
    assert "may be called through an interface (1), possible callers only, the list is never complete" in callers
    assert "app:Use  app.go:5  through app:Runner at app.go:6, app.go:7" in callers
    callees = cli.format_answer("callees", queries.callees(graph(), "app:Use"))
    assert "its interface calls may dispatch to (2), possible callees only, the list is never complete" in callees
    assert "app:T.Run  app.go:10  through app:Runner at app.go:6, app.go:7" in callees
    impact = cli.format_impact(queries.impact(graph(), "app:T.Run"))
    assert "may be reached through an interface (1), not callers; possible only, the list is never complete" in impact
    assert "app:Use  through app:Runner at app.go:6, app.go:7  may reach app:T.Run" in impact
    assert "may be" not in cli.format_answer("callers", queries.callers(graph(), "app:Use"))


def test_the_index_summary_counts_them_apart_from_the_resolved_calls():
    text = cli.format_index("repo", RepoGraph(graph(), []))
    assert "2 resolved calls, 0 unresolved calls, 0 calls to code outside the repo, 3 interface implementer edges (possible, not calls)" in text
    plain = RepoGraph(dataclasses.replace(graph(), edges=[e for e in graph().edges if e.kind != IMPLEMENTS]), [])
    assert "implementer" not in cli.format_index("repo", plain)


# ---- on a real Go repository ------------------------------------------------------------------------------------------

go_only = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")


@pytest.fixture
def go_graph(tmp_path):
    return load_repo(str(PROBE), cache_dir=str(tmp_path), lang="go").graph


@go_only
def test_a_real_go_graph_keeps_its_implements_edges_out_of_every_call_index(go_graph):
    kinds = {e.kind for e in go_graph.edges}
    assert kinds == {"call", IMPLEMENTS} or kinds == {IMPLEMENTS}
    assert go_graph.implementers_index and go_graph.dispatch_index
    for index in (go_graph.callers_index, go_graph.callees_index, go_graph.via_index):
        assert all(e.kind != IMPLEMENTS for edges in index.values() for e in edges)
    stats = go_graph.stats()
    assert stats["implements_edges"] == sum(1 for e in go_graph.edges if e.kind == IMPLEMENTS) > 0
    assert stats["edges"] == len(go_graph.edges) - stats["implements_edges"]
    assert {e.via for e in go_graph.edges if e.kind == IMPLEMENTS} == {f"{PKG}:Store", f"{PKG}:Logger"}


@go_only
def test_a_real_go_repository_answers_callers_callees_and_impact_with_the_three_keys(go_graph):
    callers = queries.callers(go_graph, f"{PKG}:Mem.Get")
    assert callers["count"] == 0 and callers["callers"] == []
    (via,) = callers["may_be_called_through_interface"]
    assert via["symbol"] == f"{PKG}:Use" and via["interface"] == f"{PKG}:Store" and len(via["call_sites"]) == 3
    callees = queries.callees(go_graph, f"{PKG}:Use")
    assert {c["symbol"] for c in callees["callees"]} == {f"{PKG}:New"}  # the one call ripple proves; the rest stay unresolved
    assert {e["symbol"] for e in callees["may_dispatch_to"]} >= {f"{PKG}:Mem.Get", f"{PKG}:Disk.Put", f"{PKG}:Std.Log"}
    assert any("a method call on the interface" in u["reason"] for u in callees["unresolved"])
    impact = queries.impact(go_graph, f"{PKG}:Mem.Get")
    assert impact["affected"] == 0 and impact["caller_count"] == 0
    assert [r["caller"] for r in impact["may_be_reached_through_interface"]] == [f"{PKG}:Use"]
    for result in (queries.callers(go_graph, f"{PKG}:Mem.Get", kinds=frozenset({"calls"})), queries.impact(go_graph, f"{PKG}:Mem.Get", kinds=frozenset({"calls"}))):
        assert not set(KEYS) & set(result)


@go_only
def test_the_cli_prints_them_on_a_real_go_repository(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RIPPLE_CACHE_DIR", str(tmp_path))
    assert cli.main(["callers", "Mem.Get", "--repo", str(PROBE), "--lang", "go"]) == 0
    out = capsys.readouterr().out
    assert "may be called through an interface (1), possible callers only, the list is never complete" in out
    assert f"{PKG}:Use" in out and f"through {PKG}:Store" in out
    assert cli.main(["callers", "Mem.Get", "--repo", str(PROBE), "--lang", "go", "--kinds", "calls"]) == 0
    assert "may be called" not in capsys.readouterr().out
    assert cli.main(["callees", "Use", "--repo", str(PROBE), "--lang", "go"]) == 0
    assert "its interface calls may dispatch to" in capsys.readouterr().out
    assert cli.main(["impact", "Mem.Get", "--repo", str(PROBE), "--lang", "go"]) == 0
    assert "may be reached through an interface" in capsys.readouterr().out
    assert cli.main(["impact", "Mem.Get", "--repo", str(PROBE), "--lang", "go", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["may_be_reached_through_interface"]
    assert cli.main(["index", str(PROBE), "--lang", "go"]) == 0
    assert "interface implementer edges (possible, not calls)" in capsys.readouterr().out


@go_only
def test_the_mcp_server_returns_the_same_keys(tmp_path):
    client = StdioClient([sys.executable, "-m", "ripple", "serve", "--repo", str(PROBE), "--lang", "go"], env={"RIPPLE_CACHE_DIR": str(tmp_path)})
    try:
        client.initialize()

        def ask(tool, **arguments):
            reply = client.request("tools/call", {"name": tool, "arguments": arguments})
            assert reply["result"]["isError"] is False, reply
            return json.loads(reply["result"]["content"][0]["text"])

        callers = ask("callers", symbol="Mem.Get")
        assert callers["count"] == 0 and callers["may_be_called_through_interface"][0]["symbol"] == f"{PKG}:Use"
        assert "never complete" in callers["may_be_called_note"]
        assert ask("callees", symbol="Use")["may_dispatch_to"]
        impact = ask("impact", symbol="Mem.Get")
        assert impact["affected"] == 0 and impact["may_be_reached_through_interface"][0]["may_reach"] == f"{PKG}:Mem.Get"
        assert "may_be_called_through_interface" not in ask("callers", symbol="Mem.Get", kinds="calls")
        assert ask("callers", symbol="Mem.Get", kinds="implements")["may_be_called_through_interface"]
        tools = {t["name"]: t for t in client.request("tools/list")["result"]["tools"]}
        assert "may_be_called_through_interface" in tools["callers"]["description"]
        assert "may_be_reached_through_interface" in tools["impact"]["description"]
        assert "may_dispatch_to" in tools["callees"]["description"]
    finally:
        assert client.close() == 0
