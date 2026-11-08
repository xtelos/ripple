"""The TypeScript scorer (python -m bench.ts_score): how it counts, and that the real fixture meets its baseline.

The fixture run needs tree-sitter and no node. The tests of the counting use a hand-made graph and truth, so
they run on a base install too.
"""

import json

import pytest

from bench import ts_score
from ripple.jsx_facts import JSX_NO_RENDERER
from ripple.model import Edge, Graph, Symbol, Unresolved

TRUTH = {
    "queries": [
        {"id": "calls-1", "edge": "calls", "symbol": "m:b", "expected": ["m:a", "m:c"], "unresolved": {"m:u": {}}},
        {"id": "calls-2", "edge": "calls", "symbol": "m:d", "expected": ["m:a"]},
        {"id": "renders-1", "edge": "renders", "symbol": "m:Child", "expected": ["m:Parent"]},
        {"id": "renderer-1", "edge": "renderer", "symbol": "m:Child", "expected": ["m:Wrap"]},
    ],
    "non_edges": [
        {"id": "non-1", "edge": "calls", "from": "m:n", "to": "m:b"},
        {"id": "non-2", "edge": "renderer", "from": "m:Parent", "to": "m:Child"},
    ],
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
    failures = ts_score.below_baseline(ts_score.score(graph(("m:u", "m:b"), ("m:n", "m:b")), TRUTH), floors())
    assert any("an unresolved caller" in f for f in failures) and any("a listed non-edge" in f for f in failures)


def test_a_miss_carries_the_reason_ripple_gave_for_the_call_site():
    found = graph(("m:a", "m:b"), unresolved=[("m:c", "obj.b", "the receiver is an unannotated parameter")])
    reasons = {m["edge"]: m["reason"] for m in calls(found)["missed"]}
    assert reasons[("m:c", "m:b")] == "unresolved at line 5: obj.b: the receiver is an unannotated parameter"
    assert reasons[("m:a", "m:d")].startswith("no unresolved call site names it")
    assert "the caller is not a symbol" in ts_score.miss_reason(Graph(), "m:ghost", "m:b")


def test_an_edge_from_a_symbol_to_itself_is_not_scored():
    assert calls(graph(("m:a", "m:a")))["reported"] == 0


def floors(calls=0.0, renders=0.0, renderer=0.0):
    """A whole baseline: every scored kind has a recall floor, and precision is always held at 1.000."""
    return {k: {"precision": 1.0, "recall": v} for k, v in (("calls", calls), ("renders", renders), ("renderer", renderer))}


def jsx_graph(*edges, unresolved=()):
    ids = {i for _, a, b, *_ in edges for i in (a, b)} | {u[0] for u in unresolved}
    return Graph(
        symbols={i: Symbol(i, "function", "m.tsx", 1, 2) for i in ids},
        edges=[Edge(a, b, "m.tsx", 1, kind) for kind, a, b in edges],
        unresolved=[Unresolved(c, "m.tsx", 5, text, reason) for c, text, reason in unresolved],
    )


def test_each_jsx_kind_is_scored_on_its_own_edges_and_a_call_is_anything_else():
    found = jsx_graph(("renders", "m:Parent", "m:Child"), ("renderer", "m:Wrap", "m:Child"), ("call", "m:a", "m:b"), ("constructor", "m:a", "m:d"))
    kinds = ts_score.score(found, TRUTH)["kinds"]
    assert (kinds["renders"]["reported"], kinds["renders"]["precision"], kinds["renders"]["recall"]) == (1, 1.0, 1.0)
    assert (kinds["renderer"]["reported"], kinds["renderer"]["precision"], kinds["renderer"]["recall"]) == (1, 1.0, 1.0)
    assert kinds["calls"]["reported"] == 2  # the two JSX edges are not calls


def test_a_renderer_the_truth_lists_as_a_non_edge_is_a_precision_failure_and_a_missing_one_is_recall_lost():
    wrong = ts_score.score(jsx_graph(("renderer", "m:Parent", "m:Child"), ("renderer", "m:Other", "m:Child")), TRUTH)["kinds"]["renderer"]
    assert wrong["claimed_non_edges"] == [("m:Parent", "m:Child")]
    assert wrong["precision"] == 0.0 and wrong["recall"] == 0.0
    failures = ts_score.below_baseline(ts_score.score(jsx_graph(("renderer", "m:Parent", "m:Child")), TRUTH), floors())
    assert any(f.startswith("renderer: 1 edges that are a listed non-edge") for f in failures)
    assert any(f.startswith("renderer precision 0.000 < baseline 1.000") for f in failures)
    # a renderer ripple did not prove is only a miss, and it says why
    missed = ts_score.score(
        jsx_graph(("renders", "m:Parent", "m:Child"), unresolved=[("m:Parent", "Child", JSX_NO_RENDERER + "it is passed to a function")]), TRUTH
    )["kinds"]["renderer"]
    assert missed["precision"] is None and missed["recall"] == 0.0
    assert missed["missed"][0]["reason"].startswith("unresolved at m.tsx:5: <Child>: the component that renders it is not proven")


def test_the_report_has_a_section_for_each_kind():
    text = ts_score.report(ts_score.score(jsx_graph(("renders", "m:Parent", "m:Child")), TRUTH))
    for kind in ("calls", "renders", "renderer"):
        assert f"[{kind}]" in text
    assert "not yet indexed" not in text


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
    assert ts_score.below_baseline(result, floors(calls=0.2)) == []
    failures = ts_score.below_baseline(result, floors(calls=0.9))
    assert failures == ["calls recall 0.333 < baseline 0.900"]


def test_a_null_or_missing_floor_in_the_baseline_is_an_error_and_precision_is_always_held_at_one():
    # a false renderer edge that the truth does not list as a non-edge: only precision can catch it
    result = ts_score.score(jsx_graph(("renderer", "m:Wrong", "m:Child"), ("renderer", "m:Wrap", "m:Child")), TRUTH)
    assert result["kinds"]["renderer"]["precision"] == 0.5
    for baseline in (
        {**floors(), "renderer": {"precision": None, "recall": 0.0}},  # a null precision used to turn its floor off
        {**floors(), "renderer": {"precision": 0.0, "recall": 0.0}},
        {**floors(), "renderer": {}},
    ):
        failures = ts_score.below_baseline(result, baseline)
        assert "renderer precision 0.500 < baseline 1.000" in failures
    missing = ts_score.below_baseline(result, {"calls": {"precision": 1.0, "recall": 0.0}})
    assert any(f.startswith("renders: the baseline has no recall floor") for f in missing)
    nulled = ts_score.below_baseline(result, {**floors(), "renderer": {"precision": 1.0, "recall": None}})
    assert any(f.startswith("renderer: the baseline has no recall floor") for f in nulled)


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


def test_the_jsx_kinds_meet_their_baselines_with_precision_exactly_one(measured):
    baseline = json.loads(ts_score.BASELINE.read_text())
    for kind in ("renders", "renderer"):
        found = measured["kinds"][kind]
        assert found["precision"] == 1.0, found["extra"]
        assert found["claimed_unresolved"] == [] and found["claimed_non_edges"] == []
        assert baseline[kind]["precision"] == 1.0
        assert found["recall"] == pytest.approx(baseline[kind]["recall"]), found["missed"]


def test_the_scorer_exits_nonzero_below_its_baseline_and_zero_at_it(measured, tmp_path, monkeypatch, capsys):
    path = tmp_path / "baseline_ts.json"
    monkeypatch.setattr(ts_score, "BASELINE", path)
    path.write_text(json.dumps(floors(calls=1.0)))  # more than the indexer finds
    assert ts_score.main([]) == 1
    assert "BELOW BASELINE" in capsys.readouterr().out
    path.write_text(json.dumps(floors(calls=0.5, renderer=1.0)))
    assert ts_score.main([]) == 0
    assert "at or above baseline" in capsys.readouterr().out
    path.write_text(json.dumps({**floors(calls=0.5), "renderer": {"precision": None, "recall": None}}))
    assert ts_score.main([]) == 1  # a null floor is an error exit, not a pass
    assert "no recall floor" in capsys.readouterr().out
    path.write_text(json.dumps(floors(calls=0.5, renders=1.01)))
    assert ts_score.main([]) == 1  # a JSX kind below its floor fails the run as a calls one does
    assert "renders recall" in capsys.readouterr().out
    assert ts_score.main(["--write-baseline"]) == 0
    written = json.loads(path.read_text())
    assert [written[k]["precision"] for k in ("calls", "renders", "renderer")] == [1.0, 1.0, 1.0]


def test_every_expected_edge_the_indexer_misses_is_explained(measured):
    missed = measured["kinds"]["calls"]["missed"]
    assert missed and all(m["reason"] for m in missed)
