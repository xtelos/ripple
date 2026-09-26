"""The benchmark's ground truth must agree with what the fixtures actually do.

The truth file is hand-written. This test runs each fixture's own test suite
under a profiler (bench/trace.py, which shares no code with ripple) and checks
every callers, callees and impact answer against the observed call edges.
If someone edits the truth to flatter ripple, or edits a fixture and forgets
the truth, this fails.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent.parent / "bench"
TRUTH = json.loads((BENCH / "ground_truth.json").read_text())["queries"]

# Edges the profiler cannot see. A dataclass has no __init__ in source (the
# generated one runs from "<string>"), so the class itself is the callee.
UNOBSERVABLE = {("S12", "shop.models.Order")}


@pytest.fixture(scope="module")
def traced(tmp_path_factory):
    edges = {}
    for fixture in sorted({q["fixture"] for q in TRUTH}):
        out = tmp_path_factory.mktemp("trace") / f"{fixture}.json"
        result = subprocess.run(
            [sys.executable, str(BENCH / "trace.py"), str(BENCH / "fixtures" / fixture), str(out)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        edges[fixture] = {tuple(e) for e in json.loads(out.read_text())}
    return edges


def observed(query, edges):
    symbol = query["symbol"]
    if query["kind"] == "callers":
        return {a for a, b in edges if b == symbol}
    if query["kind"] == "callees":
        return {b for a, b in edges if a == symbol}
    seen, frontier = set(), {symbol}
    for _ in range(query["depth"]):
        frontier = {a for a, b in edges if b in frontier} - seen - {symbol}
        seen |= frontier
    return seen


@pytest.mark.parametrize("query", TRUTH, ids=[q["id"] for q in TRUTH])
def test_truth_matches_runtime(query, traced):
    expected = {s for s in query["expected"] if (query["id"], s) not in UNOBSERVABLE}
    assert observed(query, traced[query["fixture"]]) == expected
