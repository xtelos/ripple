"""The benchmark's ground truth must agree with what the fixtures actually do.

The truth file is hand-written. This test runs each fixture's own test suite
under a profiler (bench/trace.py, which shares no code with ripple) and checks
every callers, callees and impact answer against what ran. Callers and callees are read from the
observed (caller, callee) pairs. Impact is read from the observed call stacks, not from a closure over
the pairs: a closure cannot tell that A reaches B through a wrapper that also serves C, and would call
every caller of every function a decorator wraps an impact of all of them.
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
    traces = {}
    for fixture in sorted({q["fixture"] for q in TRUTH}):
        out = tmp_path_factory.mktemp("trace") / f"{fixture}.json"
        result = subprocess.run(
            [sys.executable, str(BENCH / "trace.py"), str(BENCH / "fixtures" / fixture), str(out)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(out.read_text())
        traces[fixture] = {"edges": {tuple(e) for e in data["edges"]}, "stacks": [tuple(s) for s in data["stacks"]]}
    return traces


def closure(query, edges):
    """What a closure over the observed pairs reaches: everything that could reach the symbol if
    every observed call could follow every other. An upper bound, wrong where context matters."""
    seen, frontier = set(), {query["symbol"]}
    for _ in range(query["depth"]):
        frontier = {a for a, b in edges if b in frontier} - seen - {query["symbol"]}
        seen |= frontier
    return seen


def observed(query, trace):
    symbol, edges = query["symbol"], trace["edges"]
    if query["kind"] == "callers":
        return {a for a, b in edges if b == symbol}
    if query["kind"] == "callees":
        return {b for a, b in edges if a == symbol}
    if query["kind"] == "through_wrapper":
        # The first function above the wrapper frames, whenever symbol ran: the line that wrote its name.
        found = set()
        for stack in trace["stacks"]:
            if stack[0] == symbol:
                rest = list(stack[1:])
                while rest and rest[0] in query["wrappers"]:
                    rest.pop(0)
                if rest and len(rest) < len(stack) - 1:  # at least one wrapper frame was in between
                    found.add(rest[0])
        return found
    if query["kind"] == "missed":
        # Of the functions the truth lists as having a same-named call site ripple cannot
        # prove, the ones that really reach the symbol.
        return {a for a, b in edges if b == symbol and a in query["candidates"]}
    # Impact: the functions that were running, within depth hops, whenever symbol was called.
    return {
        name
        for stack in trace["stacks"]
        if stack[0] == symbol
        for name in stack[1 : query["depth"] + 1]
        if name != symbol
    }


@pytest.mark.parametrize("query", TRUTH, ids=[q["id"] for q in TRUTH])
def test_truth_matches_runtime(query, traced):
    trace = traced[query["fixture"]]
    expected = {s for s in query["expected"] if (query["id"], s) not in UNOBSERVABLE}
    if query["kind"] == "impact":
        # What the tests ran is the floor. A caller the tests never run through (a command chosen by
        # argv, say) is listed under "unexercised" with the reason in the note, and must be one the
        # observed calls could reach. Anything else in expected is a reach that never happens.
        unexercised = set(query.get("unexercised", []))
        assert unexercised <= closure(query, trace["edges"]), "an unexercised caller no observed call chain reaches"
        assert observed(query, trace) | unexercised == expected
    else:
        assert observed(query, trace) == expected
