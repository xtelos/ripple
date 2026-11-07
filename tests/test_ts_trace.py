"""The TypeScript ground truth must agree with what the fixture's tests actually do.

bench/ts_tracer runs the fixture under vitest and under jest, with a source transform that records
every repo-to-repo call and every JSX creator and renderer edge. The runtime tests here need node and
the fixture's packages in the cache (python -m bench.ts_tracer install); without them they are
skipped, and `pytest -rs` prints why. The rest of the file needs neither and tests the pieces:
how the cache is judged missing, how recorder output is merged, and that the comparison catches
a truth that is wrong in each direction.
"""

import copy
import shutil
import threading

import pytest

from bench import ts_truth
from bench.ts_tracer import RUNNERS, canonical, trace, typecheck, unavailable, workspace
from bench.ts_truth import KINDS, compare, edge_sets

TRUTH = ts_truth.load()
REASON = unavailable()
needs_ts = pytest.mark.skipif(REASON is not None, reason=f"TypeScript tracer unavailable, {REASON}")


# ---- runtime: the truth against the traces

@pytest.fixture(scope="module", params=RUNNERS)
def traced(request):
    return trace(request.param)


@needs_ts
def test_the_trace_and_the_truth_say_the_same_thing(traced):
    assert compare(TRUTH, traced["edges"]) == []


@needs_ts
def test_untraceable_edges_really_are_not_observed(traced):
    for kind, sets in edge_sets(TRUTH).items():
        seen = {tuple(e) for e in traced["edges"][kind]}
        assert not sets["untraceable"] & seen, kind


@needs_ts
def test_two_traces_at_once_each_match_the_truth():
    results, errors = [], []

    def run():
        try:
            results.append(trace("vitest")["edges"])
        except Exception as error:  # reported below, with the other thread's outcome
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(results) == 2
    assert all(compare(TRUTH, edges) == [] for edges in results)


@needs_ts
def test_the_recorder_reported_nothing_it_could_not_place(traced):
    bad = {k for k in traced["diag"] if not k.startswith("react-version") and k != "host-elements"}
    assert bad == set(), traced["diag"]


@needs_ts
def test_vitest_and_jest_observe_the_same_edges():
    assert trace("vitest")["edges"] == trace("jest")["edges"]


@needs_ts
def test_the_fixture_is_valid_typescript():
    result = typecheck()
    assert result.returncode == 0, result.stdout + result.stderr


# ---- the pieces, without node

def test_a_missing_cache_is_reported_with_the_command_that_creates_it(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setenv("RIPPLE_TS_CACHE", str(tmp_path / "none"))
    reason = unavailable()
    assert "python -m bench.ts_tracer install" in reason and str(tmp_path / "none") in reason


def test_a_missing_node_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert "node" in unavailable()


def test_a_cache_installed_from_another_lockfile_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setenv("RIPPLE_TS_CACHE", str(tmp_path))
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / ".installed").write_text("0" * 64 + "\n")
    assert "install" in unavailable()


def test_two_runs_never_share_a_work_dir_and_clean_up_after_themselves(monkeypatch, tmp_path):
    shared = tmp_path / "work" / "node_modules"
    shared.mkdir(parents=True)
    monkeypatch.setenv("RIPPLE_TS_CACHE", str(tmp_path))
    with workspace() as first, workspace() as second:
        assert first != second and first.parent == second.parent == tmp_path / "runs"
        for run in (first, second):
            assert (run / "src" / "inventory.ts").is_file() and (run / "tests" / "ui.test.tsx").is_file()
            assert (run / "node_modules").is_symlink() and (run / "node_modules").resolve() == shared.resolve()
        (first / "src" / "inventory.ts").unlink()  # what a second sync into a shared dir would have done to a running first
        assert (second / "src" / "inventory.ts").is_file()
    assert not first.exists() and not second.exists() and shared.is_dir()  # the shared packages are not touched


def test_a_failing_run_still_removes_its_work_dir(monkeypatch, tmp_path):
    (tmp_path / "work" / "node_modules").mkdir(parents=True)
    monkeypatch.setenv("RIPPLE_TS_CACHE", str(tmp_path))
    with pytest.raises(RuntimeError):
        with workspace() as run:
            raise RuntimeError("the runner failed")
    assert not run.exists()


def test_recorder_files_merge_into_sorted_repo_edges_only():
    raw = [
        {"diag": {"host-elements": 2}, "edges": [
            {"kind": "call", "from": "src/b:f", "to": "src/a:g", "site": "src/b.ts:3:1", "count": 2},
            {"kind": "call", "from": "src/a:g", "to": "src/a:g", "site": "src/a.ts:9:1", "count": 1},  # recursion
            {"kind": "creator", "from": "src/ui/P:P", "to": "?anonymous", "site": "-", "count": 1},  # not a repo component
            {"kind": "renderer", "from": "<root>", "to": "src/ui/P:P", "site": "-", "count": 1},  # the mount root
            {"kind": "creator", "from": "src/ui/P:P", "to": "src/ui/C:C", "site": "src/ui/P.tsx:4:5", "count": 1},
        ]},
        {"diag": {"host-elements": 1}, "edges": [
            {"kind": "call", "from": "src/b:f", "to": "src/a:g", "site": "src/b.ts:7:1", "count": 1},
            {"kind": "call", "from": "tests/t.test:a test", "to": "src:barrel", "site": "tests/t.test.ts:2:1", "count": 1},
        ]},
    ]
    merged = canonical(raw)
    assert merged["edges"] == {
        "calls": [["src/b:f", "src/a:g"], ["tests/t.test:a test", "src:barrel"]],
        "renders": [["src/ui/P:P", "src/ui/C:C"]],
        "renderer": [],
    }
    assert merged["sites"]["calls"]["src/b:f -> src/a:g"] == ["src/b.ts:3:1", "src/b.ts:7:1"]
    assert merged["diag"] == {"host-elements": 3}


# ---- the comparison catches a wrong truth, in each direction

def perfect_trace(truth):
    """What a tracer that saw exactly what the truth says would report."""
    return {
        kind: sorted([a, b] for a, b in (s["expected"] - s["untraceable"]) | s["unresolved"])
        for kind, s in edge_sets(truth).items()
    }


def test_the_comparison_accepts_a_trace_that_matches():
    assert compare(TRUTH, perfect_trace(TRUTH)) == []


def test_a_truth_edge_nobody_observed_is_reported():
    seen = perfect_trace(TRUTH)
    seen["calls"].remove(["src/util/format:label", "src/util/format:formatQty"])
    assert compare(TRUTH, seen) == ["calls: in the truth but never observed: src/util/format:label -> src/util/format:formatQty"]


def test_an_observed_edge_the_truth_lacks_is_reported():
    seen = perfect_trace(TRUTH)
    seen["renders"].append(["src/ui/Page:Page", "src/ui/Stock:FullMark"])
    assert compare(TRUTH, seen) == ["renders: observed but not in the truth: src/ui/Page:Page -> src/ui/Stock:FullMark"]


def test_an_unresolved_edge_must_still_be_observed():
    seen = perfect_trace(TRUTH)
    seen["calls"].remove(["src/dynamic:dispatch", "src/dynamic:pickSku"])
    assert compare(TRUTH, seen) == ["calls: in the truth but never observed: src/dynamic:dispatch -> src/dynamic:pickSku"]


def test_a_non_edge_that_was_observed_is_reported():
    seen = perfect_trace(TRUTH)
    seen["renderer"].append(["src/ui/Page:Page", "src/ui/Stock:StockList"])
    problems = compare(TRUTH, seen)
    assert "renderer: a non-edge was observed: src/ui/Page:Page -> src/ui/Stock:StockList" in problems


def test_an_untraceable_edge_that_was_observed_is_a_wrong_mark():
    seen = perfect_trace(TRUTH)
    seen["calls"].append(["src/inventory:Inventory.restockAll", "src/inventory:Inventory.trace"])
    assert compare(TRUTH, seen) == ["calls: marked untraceable but observed: src/inventory:Inventory.restockAll -> src/inventory:Inventory.trace"]


def test_swapping_an_expected_caller_in_the_truth_is_reported():
    wrong = copy.deepcopy(TRUTH)
    query = next(q for q in wrong["queries"] if q["id"] == "calls-26")
    query["expected"][0] = "src/inventory:Inventory.labels"  # not the caller
    problems = compare(wrong, perfect_trace(TRUTH))
    assert any("observed but not in the truth: src/inventory:Inventory.addAll -> src/inventory:Inventory.add" in p for p in problems)
    assert any("in the truth but never observed: src/inventory:Inventory.labels -> src/inventory:Inventory.add" in p for p in problems)


def test_every_kind_is_compared():
    assert set(perfect_trace(TRUTH)) == set(KINDS) and all(perfect_trace(TRUTH)[k] for k in KINDS)
