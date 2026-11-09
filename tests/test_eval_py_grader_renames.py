"""The Python grader's weakened-test check when an agent renames a test function.

A task whose reference solution edits a test function was graded by that function's NAME, so an agent
that renamed the test along with the calls in it (task 05 renames `parse_amount`, and a test named
`test_parse_amount_...` is a test of the renamed function) was marked down as having dropped the new
name, though the test was all there under another name. The grader now takes a reference test whose name
is gone to be an unclaimed test of the agent's, in the same file, that carries every identifier the
reference test carries (its own name aside), one agent test per reference test at most. The node grader
does the same for a title (tests/test_eval_node_check.py).
"""

import ast
from pathlib import Path

import pytest

from eval.check import check_task, weakened_tests
from eval.loader import Task, copy_fixture, apply_solution, load_tasks

TASKS = {t.id: t for t in load_tasks()}
LEDGER_05 = "05-ledger-drop-amount-alias"
LEDGER_07 = "07-ledger-rename-account-apply"
SHOP_02 = "02-shop-rename-price-order"


def task_of(task_id: str) -> Task:
    task = TASKS[task_id]
    if not task.available:
        pytest.skip("needs the fixture")
    return task


def rename_function(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(f"def {old}(") == 1, f"{path}: def {old}( found {text.count(f'def {old}(')} times"
    path.write_text(text.replace(f"def {old}(", f"def {new}("))


def delete_function(path: Path, name: str) -> None:
    source = path.read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[: node.lineno - 1] + lines[node.end_lineno :]))


def gut_function(path: Path, name: str) -> None:
    source = path.read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[: node.body[0].lineno - 1] + ["    pass\n"] + lines[node.end_lineno :]))


def solved(task: Task, tmp_path: Path) -> Path:
    repo = copy_fixture(task, tmp_path / "repo")
    apply_solution(task, repo)
    return repo


# ---------------------------------------------------------------- real check_task runs on the fixture tasks


def test_task_05_with_the_renamed_test_is_graded_success(tmp_path):
    """The paid P0 run as it happened: the reference solution, the test of `parse_amount` renamed to
    `test_parse_money_...` with its calls. The suite passed, the hidden test passed, and the grade failed."""
    task = task_of(LEDGER_05)
    repo = solved(task, tmp_path)
    rename_function(repo / "tests/test_money.py", "test_parse_amount_handles_symbols_commas_and_signs", "test_parse_money_handles_symbols_commas_and_signs")
    verdict = check_task(task, repo)
    assert verdict["success"] and verdict["tests_lost"] == 0 and verdict["lost_tests"] == [], verdict


@pytest.mark.parametrize(
    "task_id, test_file, old, new",
    [
        (LEDGER_07, "tests/test_accounts.py", "test_apply_returns_the_new_balance", "test_post_returns_the_new_balance"),
        (SHOP_02, "tests/test_pricing.py", "test_percent_discount_and_tax", "test_quote_with_percent_discount_and_tax"),
    ],
    ids=["07-apply-to-post", "02-price-to-quote"],
)
def test_other_fixture_tasks_with_a_renamed_edited_test_are_graded_success(task_id, test_file, old, new, tmp_path):
    task = task_of(task_id)
    repo = solved(task, tmp_path)
    assert check_task(task, repo)["success"]
    rename_function(repo / test_file, old, new)
    verdict = check_task(task, repo)
    assert verdict["success"] and verdict["tests_lost"] == 0, verdict


def test_task_05_deleting_the_edited_test_is_still_marked_down(tmp_path):
    task = task_of(LEDGER_05)
    repo = solved(task, tmp_path)
    delete_function(repo / "tests/test_money.py", "test_parse_amount_handles_symbols_commas_and_signs")
    verdict = check_task(task, repo)
    assert not verdict["success"], verdict
    assert verdict["lost_tests"] == ["tests/test_money.py::test_parse_amount_handles_symbols_commas_and_signs (dropped parse_money)"], verdict


def test_task_05_renaming_the_edited_test_and_gutting_it_is_still_marked_down(tmp_path):
    task = task_of(LEDGER_05)
    repo = solved(task, tmp_path)
    path = repo / "tests/test_money.py"
    gut_function(path, "test_parse_amount_handles_symbols_commas_and_signs")
    rename_function(path, "test_parse_amount_handles_symbols_commas_and_signs", "test_parse_money_handles_symbols_commas_and_signs")
    verdict = check_task(task, repo)
    assert not verdict["success"], verdict
    assert verdict["lost_tests"] == ["tests/test_money.py::test_parse_amount_handles_symbols_commas_and_signs (dropped parse_money)"], verdict


def test_task_05_an_unrelated_test_that_has_the_new_name_does_not_rescue_a_deleted_one(tmp_path):
    """The renamed test is gone; another test (of format, which does not use parse_money) is not a stand-in."""
    task = task_of(LEDGER_05)
    repo = solved(task, tmp_path)
    path = repo / "tests/test_money.py"
    delete_function(path, "test_parse_amount_handles_symbols_commas_and_signs")
    path.write_text(path.read_text() + "\n\ndef test_format_amount_again():\n    assert format_amount(Money(5), 4)\n")
    verdict = check_task(task, repo)
    assert not verdict["success"] and any("test_parse_amount_handles" in t for t in verdict["lost_tests"]), verdict


# ---------------------------------------------------------------- the analysis alone, on small files

START = (
    "from pkg import a\n\n\n"
    "def test_one():\n    assert a.old() == 1\n\n\n"
    "def test_two():\n    assert a.keep() == 2\n"
)
REFERENCE = START.replace("a.old()", "a.fresh()")


def build(tmp_path: Path, agent_text: str | None, start: str = START, reference: str = REFERENCE):
    fixture, ref, agent = tmp_path / "fixture", tmp_path / "reference", tmp_path / "agent"
    for root, text in ((fixture, start), (ref, reference)):
        (root / "tests").mkdir(parents=True)
        (root / "tests/test_a.py").write_text(text)
    (agent / "tests").mkdir(parents=True)
    if agent_text is not None:
        (agent / "tests/test_a.py").write_text(agent_text)
    task = Task(id="t", fixture=fixture, prompt="p", min_tests=1, why="w", dir=tmp_path)
    return task, agent, ref


def test_the_same_edit_is_not_weak(tmp_path):
    assert weakened_tests(*build(tmp_path, REFERENCE)) == []


def test_a_test_renamed_with_the_new_name_still_in_it_is_not_weak(tmp_path):
    assert weakened_tests(*build(tmp_path, REFERENCE.replace("test_one", "test_first"))) == []


def test_a_deleted_test_is_weak(tmp_path):
    text = "from pkg import a\n\n\ndef test_two():\n    assert a.keep() == 2\n"
    assert weakened_tests(*build(tmp_path, text)) == ["tests/test_a.py::test_one (dropped fresh)"]


def test_a_missing_file_is_weak(tmp_path):
    assert weakened_tests(*build(tmp_path, None)) == ["tests/test_a.py::test_one (dropped fresh)"]


def test_the_name_kept_and_the_use_dropped_is_weak(tmp_path):
    assert weakened_tests(*build(tmp_path, START)) == ["tests/test_a.py::test_one (dropped fresh)"]


def test_renamed_and_gutted_is_weak(tmp_path):
    assert weakened_tests(*build(tmp_path, START.replace("test_one", "test_first"))) == ["tests/test_a.py::test_one (dropped fresh)"]


def test_renamed_and_the_new_name_replaced_by_another_is_weak(tmp_path):
    text = REFERENCE.replace("test_one", "test_first").replace("a.fresh()", "a.other()")
    assert weakened_tests(*build(tmp_path, text)) == ["tests/test_a.py::test_one (dropped fresh)"]


def test_a_test_gutted_under_its_own_name_is_not_rescued_by_another_test_that_has_the_name(tmp_path):
    """`test_one` keeps its name and loses the new one; a different test of the agent's has it. Still weak: a name
    that is still there is never second-guessed."""
    text = START + "\n\ndef test_extra():\n    assert a.fresh() == 1\n"
    assert weakened_tests(*build(tmp_path, text)) == ["tests/test_a.py::test_one (dropped fresh)"]


def test_a_stand_in_must_carry_everything_the_reference_test_carries(tmp_path):
    """The renamed test has the new name but lost another identifier the reference test uses."""
    start = "from pkg import a, b\n\n\ndef test_one():\n    assert a.old() == b.size\n"
    reference = start.replace("a.old()", "a.fresh()")
    agent = "from pkg import a, b\n\n\ndef test_first():\n    assert a.fresh() == 1\n"
    assert weakened_tests(*build(tmp_path, agent, start, reference)) == ["tests/test_a.py::test_one (dropped fresh)"]
    whole = "from pkg import a, b\n\n\ndef test_first():\n    assert a.fresh() == b.size\n"
    assert weakened_tests(*build(tmp_path / "whole", whole, start, reference)) == []


def test_a_stand_in_is_looked_for_in_the_same_file_only(tmp_path):
    task, agent, reference = build(tmp_path, "def test_unrelated():\n    pass\n")
    (agent / "tests/test_b.py").write_text(REFERENCE.replace("test_one", "test_first"))
    assert weakened_tests(task, agent, reference) == ["tests/test_a.py::test_one (dropped fresh)"]


METHOD_START = "from pkg import a\n\n\nclass TestA:\n    def test_one(self):\n        assert a.old() == 1\n"
METHOD_REFERENCE = METHOD_START.replace("a.old()", "a.fresh()")


def test_a_method_renamed_within_its_class_is_not_weak(tmp_path):
    agent = METHOD_REFERENCE.replace("test_one", "test_first")
    assert weakened_tests(*build(tmp_path, agent, METHOD_START, METHOD_REFERENCE)) == []
    gutted = agent.replace("a.fresh()", "a.old()")
    assert weakened_tests(*build(tmp_path / "gutted", gutted, METHOD_START, METHOD_REFERENCE)) == ["tests/test_a.py::TestA::test_one (dropped fresh)"]


def test_a_method_moved_out_to_a_module_level_function_is_still_weak(tmp_path):
    """Deliberate: a move is not a rename. `self` is among the identifiers the reference method carries and a
    plain function lacks it, so the moved test is not found again (it was marked down before stand-ins too)."""
    agent = "from pkg import a\n\n\ndef test_first():\n    assert a.fresh() == 1\n"
    assert weakened_tests(*build(tmp_path, agent, METHOD_START, METHOD_REFERENCE)) == ["tests/test_a.py::TestA::test_one (dropped fresh)"]


def test_a_helper_or_fixture_that_uses_the_new_name_is_not_a_stand_in_for_a_deleted_test(tmp_path):
    """Only a test can stand in for a test: a fixture or a `_helper` that happens to use the same identifiers is not one."""
    helper = "from pkg import a\n\n\ndef _helper():\n    return a.fresh() == 1\n"
    assert weakened_tests(*build(tmp_path, helper)) == ["tests/test_a.py::test_one (dropped fresh)"]
    fixture = "import pytest\nfrom pkg import a\n\n\n@pytest.fixture\ndef fresh_value():\n    return a.fresh() == 1\n"
    assert weakened_tests(*build(tmp_path / "fixture", fixture)) == ["tests/test_a.py::test_one (dropped fresh)"]
    both = helper + "\n\ndef test_first():\n    assert a.fresh() == 1\n"
    assert weakened_tests(*build(tmp_path / "both", both)) == []


TWINS_START = "from pkg import a\n\n\ndef test_one():\n    assert a.old() == 1\n\n\ndef test_again():\n    assert a.old() == 1\n"
TWINS_REFERENCE = TWINS_START.replace("a.old()", "a.fresh()")


def test_one_agent_test_stands_in_for_only_one_reference_test(tmp_path):
    """Two reference tests carry the same names: a single renamed test of the agent's cannot be both."""
    one = "from pkg import a\n\n\ndef test_both():\n    assert a.fresh() == 1\n"
    weak = weakened_tests(*build(tmp_path, one, TWINS_START, TWINS_REFERENCE))
    assert len(weak) == 1 and weak[0].endswith("(dropped fresh)"), weak
    two = one + "\n\ndef test_second():\n    assert a.fresh() == 1\n"
    assert weakened_tests(*build(tmp_path / "two", two, TWINS_START, TWINS_REFERENCE)) == []


def test_a_test_that_is_there_by_name_is_not_also_a_stand_in_for_a_deleted_one(tmp_path):
    """`test_one` is kept whole and `test_again`, which uses the same names, is deleted: the one that is there is not the other."""
    kept = "from pkg import a\n\n\ndef test_one():\n    assert a.fresh() == 1\n"
    assert weakened_tests(*build(tmp_path, kept, TWINS_START, TWINS_REFERENCE)) == ["tests/test_a.py::test_again (dropped fresh)"]


def test_stand_ins_are_paired_so_that_each_reference_test_gets_one_that_covers_it(tmp_path):
    """The smaller reference test fits both agent tests, the larger only one: the pairing may not give the larger one away."""

    def make(fn_small, fn_large, one, two, order=("small", "large")):
        small = f"def {fn_small}():\n    assert a.{one}() == 1\n"
        large = f"def {fn_large}():\n    assert a.{one}() == 1\n    assert a.{two}() == 1\n"
        return "from pkg import a\n\n\n" + "\n\n".join({"small": small, "large": large}[o] for o in order)

    start = make("test_p", "test_q", "old", "old2")
    reference = make("test_p", "test_q", "fresh", "extra")
    agent = make("test_little", "test_big", "fresh", "extra", order=("large", "small"))
    assert weakened_tests(*build(tmp_path, agent, start, reference)) == []


def test_an_unparseable_agent_file_is_weak_not_an_error(tmp_path):
    assert weakened_tests(*build(tmp_path, "def test_one(:\n")) == ["tests/test_a.py::test_one (dropped fresh)"]
