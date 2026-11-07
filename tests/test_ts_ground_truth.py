"""The TypeScript ground truth must be well formed and name things the fixture really contains.

This needs no node: it only reads bench/ground_truth_ts.json and the fixture's text. Whether the truth
agrees with what the fixture does at run time is tests/test_ts_trace.py.
"""

import copy
import json

import pytest

from bench import ts_truth

TRUTH = ts_truth.load()


def symbols(truth):
    found = set()
    for q in truth["queries"]:
        found |= {q["symbol"], *q["expected"], *q.get("unresolved", {}), *q.get("untraceable", {})}
    for n in truth["non_edges"]:
        found |= {n["from"], n["to"]}
    return found


def test_every_symbol_the_truth_names_is_declared_in_the_fixture():
    problems = {s: p for s in sorted(symbols(TRUTH)) if (p := ts_truth.symbol_problem(s))}
    assert problems == {}


@pytest.mark.parametrize(
    "bad",
    [
        "src/util/math:nope",  # not declared in an existing file
        "src/nope:clamp",  # no such module
        "src/models/item:Item.nope",  # a member the class does not have
        "src/models/item:Item.clamp",  # used in the file but not declared there
        "tests/ui.test:components > renders nothing at all",  # no such test title
    ],
)
def test_the_symbol_check_rejects_a_symbol_the_fixture_lacks(bad):
    assert ts_truth.symbol_problem(bad)


def test_ids_are_unique_and_every_query_has_its_parts():
    ids = [q["id"] for q in TRUTH["queries"]] + [n["id"] for n in TRUTH["non_edges"]]
    assert len(ids) == len(set(ids))
    for q in TRUTH["queries"]:
        assert q["kind"] == "callers" and q["edge"] in ts_truth.KINDS, q["id"]
        assert q["fixture"] == "warehouse-ts" and q["group"] and q["note"], q["id"]
        assert isinstance(q["expected"], list) and len(q["expected"]) == len(set(q["expected"])), q["id"]
    for n in TRUTH["non_edges"]:
        assert n["edge"] in ts_truth.KINDS and n["from"] != n["to"] and n["note"], n["id"]


def test_unresolved_and_untraceable_callers_each_carry_a_closed_why_and_a_site():
    assert ts_truth.validate(TRUTH) == []
    for q in TRUTH["queries"]:
        unresolved, untraceable = q.get("unresolved", {}), q.get("untraceable", {})
        assert set(untraceable) <= set(q["expected"]), q["id"]  # untraceable marks an expected caller the tracer cannot see
        assert not set(unresolved) & set(q["expected"]), q["id"]  # a caller is resolved or unresolved, never both
        assert all(e["why"] in ts_truth.UNRESOLVED_WHY for e in unresolved.values()), q["id"]
        assert all(e["why"] in ts_truth.UNTRACEABLE_WHY for e in untraceable.values()), q["id"]


def test_the_loader_rejects_a_bucket_entry_without_a_closed_why_and_a_site(tmp_path):
    for mutate in (
        lambda e: e.update(why="because"),  # not in the closed set
        lambda e: e.pop("why"),
        lambda e: e.pop("site"),
        lambda e: e.update(site="src/dynamic.ts"),  # no line
        lambda e: e.update(site="src/nowhere.ts:3"),  # no such file
        lambda e: e.update(site="src/dynamic.ts:9999"),  # past the end
        lambda e: e.update(why="debug-flag"),  # an untraceable why in the unresolved bucket
        lambda e: e.pop("reason"),
    ):
        bad = copy.deepcopy(TRUTH)
        mutate(next(q for q in bad["queries"] if q["id"] == "calls-44")["unresolved"]["src/dynamic:run"])
        assert ts_truth.validate(bad), mutate
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            ts_truth.load(path)
    bad = copy.deepcopy(TRUTH)
    next(q for q in bad["queries"] if q["id"] == "calls-33")["untraceable"]["src/inventory:Inventory.restockAll"]["why"] = "decorator"
    assert ts_truth.validate(bad)  # a resolved-bucket why in the untraceable bucket


def line_of(path, text):
    return next(i for i, ln in enumerate((ts_truth.FIXTURE / path).read_text().splitlines(), 1) if text in ln)


def query(truth, qid):
    return next(q for q in truth["queries"] if q["id"] == qid)


def test_the_truth_as_written_has_no_problems():
    assert ts_truth.truth_problems(TRUTH) == []


def test_a_caller_moved_from_expected_to_unresolved_is_reported():
    bad = copy.deepcopy(TRUTH)
    q = query(bad, "calls-12")
    q["expected"].remove("src/inventory:describeItem")
    q["unresolved"] = {"src/inventory:describeItem": {
        "why": "interface-typed-receiver", "site": f"src/inventory.ts:{line_of('src/inventory.ts', 'item.describe()')}", "reason": "x"}}
    assert ts_truth.truth_problems(bad)


def test_a_caller_moved_from_unresolved_to_expected_is_reported():
    bad = copy.deepcopy(TRUTH)
    q = query(bad, "calls-44")
    del q["unresolved"]
    q["expected"] = ["src/dynamic:run"]
    assert ts_truth.truth_problems(bad)


def test_an_invented_untraceable_edge_is_reported():
    bad = copy.deepcopy(TRUTH)
    q = query(bad, "calls-01")
    q["expected"].append("src/ui/Page:Page")
    q["untraceable"] = {"src/ui/Page:Page": {
        "why": "debug-flag", "site": f"src/ui/Page.tsx:{line_of('src/ui/Page.tsx', 'Panel title')}", "reason": "x"}}
    assert ts_truth.truth_problems(bad)


@pytest.mark.parametrize("why, qid, caller", [
    ("computed-key", "calls-44", "src/dynamic:run"),  # an interface call is not a computed key
    ("decorator", "calls-40", "src/dynamic:dispatch"),
    ("method-by-name", "calls-40", "src/dynamic:dispatch"),  # a table lookup, not a method of the receiver
    ("interface-typed-receiver", "calls-28", "src/dynamic:callByName"),
    ("decorator-wrapper", "calls-50", "src/dynamic"),
])
def test_a_why_whose_marker_is_not_at_the_site_is_reported(why, qid, caller):
    bad = copy.deepcopy(TRUTH)
    entry = query(bad, qid)["unresolved"][caller]
    entry["why"] = why
    assert ts_truth.truth_problems(bad), (why, qid)


def test_a_debug_flag_site_with_no_flag_guard_is_reported():
    bad = copy.deepcopy(TRUTH)
    entry = query(bad, "calls-33")["untraceable"]["src/inventory:Inventory.restockAll"]
    entry["site"] = f"src/inventory.ts:{line_of('src/inventory.ts', 'item.restock(by)')}"
    assert ts_truth.truth_problems(bad)


def test_an_expected_caller_whose_body_never_names_the_callee_is_reported():
    bad = copy.deepcopy(TRUTH)
    query(bad, "calls-40")["expected"] = ["src/dynamic:PickHandler.handle", "src/dynamic:dispatch"]  # dispatch only reaches it by key
    assert ts_truth.truth_problems(bad)
def test_no_non_edge_is_also_claimed_as_an_edge():
    for kind, sets in ts_truth.edge_sets(TRUTH).items():
        assert not sets["non_edge"] & (sets["expected"] | sets["unresolved"]), kind


def test_a_callee_is_asked_about_once_per_edge_kind():
    asked = [(q["edge"], q["symbol"]) for q in TRUTH["queries"]]
    assert len(asked) == len(set(asked))


def test_every_fixture_source_file_that_declares_something_is_in_the_truth():
    named = {s.partition(":")[0] for s in symbols(TRUTH)}
    for path in sorted(ts_truth.FIXTURE.glob("src/**/*.ts*")) + sorted(ts_truth.FIXTURE.glob("tests/*.ts*")):
        if path.name.startswith("index."):
            continue  # a barrel only re-exports
        module = path.relative_to(ts_truth.FIXTURE).as_posix()[: -len(path.suffix)]
        assert module in named, f"{path.relative_to(ts_truth.FIXTURE)} appears nowhere in the truth"


def test_the_fixture_stays_small_enough_to_check_by_hand():
    sources = [p for p in ts_truth.FIXTURE.glob("src/**/*.ts*")]
    assert len(sources) <= 20, len(sources)
