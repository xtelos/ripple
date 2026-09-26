import json

from bench.__main__ import BASELINE, below_baseline, score
from ripple.indexer import build_graph
from ripple.queries import find_symbol


def test_current_numbers_meet_the_checked_in_baseline():
    assert below_baseline(score(), json.loads(BASELINE.read_text())) == []


def test_a_drop_below_baseline_is_reported():
    result = {"groups": {"overall": {"precision": 0.90, "recall": 0.70}}}
    failures = below_baseline(result, {"overall": {"precision": 0.95, "recall": 0.70}})
    assert failures == ["overall precision 0.900 < baseline 0.950"]


def test_every_query_names_a_symbol_that_exists():
    # A typo in the truth file would otherwise score as a silent miss.
    truth = json.loads((BASELINE.parent / "ground_truth.json").read_text())["queries"]
    graphs = {}
    for q in truth:
        graph = graphs.setdefault(q["fixture"], build_graph(BASELINE.parent / "fixtures" / q["fixture"]))
        assert find_symbol(graph, q["symbol"]).get("symbol") == q["symbol"], q["id"]
        for expected in q["expected"]:
            assert expected in graph.symbols, (q["id"], expected)
