"""The TypeScript scorer (python -m bench.ts_score): how it counts, and that the real fixture meets its baseline.

The fixture run needs tree-sitter and no node. The tests of the counting use a hand-made graph and truth, so
they run on a base install too.
"""

import json

import pytest

from bench import ts_score
from ripple.model import Edge, Graph, Symbol, Unresolved

TRUTH = {
    "queries": [
        {"id": "calls-1", "edge": "calls", "symbol": "m:b", "expected": ["m:a", "m:c"], "unresolved": {"m:u": {}}},
        {"id": "calls-2", "edge": "calls", "symbol": "m:d", "expected": ["m:a"]},
        {"id": "renders-1", "edge": "renders", "symbol": "m:Child", "expected": ["m:Parent"]},
    ],
    "non_edges": [{"id": "non-1", "edge": "calls", "from": "m:n", "to": "m:b"}],
}


def graph(*pairs, unresolved=()):
    ids = {i for pair in pairs for i in pair} | {u[0] for u in unresolved}
    return Graph(
        symbols={i: Symbol(i, "function", "m.ts", 1, 2) for i in ids},
        edges=[Edge(a, b, "m.ts", 1) for a, b in pairs],
        unresolved=[Unresolved(c, "m.ts", 5, text, reason) for c, text, reason in unresolved],
    )


def calls(found, **kw):
    return ts_score.score(found, TRUTH)["kinds"]["calls"] | kw


def test_precision_is_the_share_of_reported_edges_that_are_expected_and_recall_the_share_found():
    result = calls(graph(("m:a", "m:b"), ("m:a", "m:d"), ("m:x", "m:y")))
    assert (result["reported"], result["true_positives"], result["expected"]) == (3, 2, 3)
    assert result["precision"] == pytest.approx(2 / 3) and result["recall"] == pytest.approx(2 / 3)
    assert result["extra"] == [("m:x", "m:y")]
    assert [m["edge"] for m in result["missed"]] == [("m:c", "m:b")]


def test_an_unresolved_caller_and_a_listed_non_edge_that_ripple_reports_are_precision_failures():
    result = calls(graph(("m:a", "m:b"), ("m:c", "m:b"), ("m:u", "m:b"), ("m:n", "m:b")))
    assert result["claimed_unresolved"] == [("m:u", "m:b")]
    assert result["claimed_non_edges"] == [("m:n", "m:b")]
    assert result["precision"] == 0.5 and result["recall"] == pytest.approx(2 / 3)
    failures = ts_score.below_baseline(ts_score.score(graph(("m:u", "m:b"), ("m:n", "m:b")), TRUTH), {"calls": {}})
    assert any("an unresolved caller" in f for f in failures) and any("a listed non-edge" in f for f in failures)


def test_a_miss_carries_the_reason_ripple_gave_for_the_call_site():
    found = graph(("m:a", "m:b"), unresolved=[("m:c", "obj.b", "the receiver is an unannotated parameter")])
    reasons = {m["edge"]: m["reason"] for m in calls(found)["missed"]}
    assert reasons[("m:c", "m:b")] == "unresolved at line 5: obj.b: the receiver is an unannotated parameter"
    assert reasons[("m:a", "m:d")].startswith("no unresolved call site names it")
    assert "the caller is not a symbol" in ts_score.miss_reason(Graph(), "m:ghost", "m:b")


def test_an_edge_from_a_symbol_to_itself_is_not_scored():
    assert calls(graph(("m:a", "m:a")))["reported"] == 0


def test_the_jsx_kinds_are_reported_as_not_yet_indexed_and_are_not_counted():
    result = ts_score.score(graph(("m:Parent", "m:Child")), TRUTH)
    assert result["kinds"]["renders"] == {"status": "not yet indexed"}
    assert result["kinds"]["renderer"] == {"status": "not yet indexed"}
    assert "renders   not yet indexed (not counted)" in ts_score.report(result)
    assert result["kinds"]["calls"]["reported"] == 1  # the render edge, were one reported, is not a call


def test_without_the_parser_the_scorer_says_what_to_install_and_exits_2(monkeypatch, capsys):
    import sys

    from ripple import ts_indexer

    monkeypatch.setattr(ts_indexer, "_PARSERS", None)
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_typescript", None)
    assert ts_score.main([]) == 2
    assert "pip install 'ripple-code[typescript]'" in capsys.readouterr().err


def test_below_baseline_names_the_metric_that_dropped():
    result = ts_score.score(graph(("m:a", "m:b")), TRUTH)
    assert ts_score.below_baseline(result, {"calls": {"precision": 1.0, "recall": 0.2}}) == []
    failures = ts_score.below_baseline(result, {"calls": {"precision": 1.0, "recall": 0.9}})
    assert failures == ["calls recall 0.333 < baseline 0.900"]


# ---- the fixture

@pytest.fixture(scope="module")
def measured():
    pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
    pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
    return ts_score.run()


def test_the_fixture_meets_the_baseline_and_precision_is_exactly_one(measured):
    calls = measured["kinds"]["calls"]
    assert calls["precision"] == 1.0, calls["extra"]
    assert calls["claimed_unresolved"] == [] and calls["claimed_non_edges"] == []
    baseline = json.loads(ts_score.BASELINE.read_text())
    assert ts_score.below_baseline(measured, baseline) == []
    # the floor is the number last measured: a better recall is a reason to write the baseline again
    assert calls["recall"] == pytest.approx(baseline["calls"]["recall"])
    assert baseline["calls"]["precision"] == 1.0


def test_the_scorer_exits_nonzero_below_its_baseline_and_zero_at_it(measured, tmp_path, monkeypatch, capsys):
    path = tmp_path / "baseline_ts.json"
    monkeypatch.setattr(ts_score, "BASELINE", path)
    path.write_text(json.dumps({"calls": {"precision": 1.0, "recall": 1.0}}))  # more than the indexer finds
    assert ts_score.main([]) == 1
    assert "BELOW BASELINE" in capsys.readouterr().out
    path.write_text(json.dumps({"calls": {"precision": 1.0, "recall": 0.5}}))
    assert ts_score.main([]) == 0
    assert "at or above baseline" in capsys.readouterr().out
    assert ts_score.main(["--write-baseline"]) == 0
    assert json.loads(path.read_text())["calls"]["precision"] == 1.0


def test_every_expected_edge_the_indexer_misses_is_explained(measured):
    missed = measured["kinds"]["calls"]["missed"]
    assert missed and all(m["reason"] for m in missed)
