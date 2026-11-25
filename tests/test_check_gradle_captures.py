"""The verdict `check` gives for runs RECORDED from real Gradle 9.9.1, replayed where no Gradle exists.

Each capture under tests/fixtures/gradle_check/captures holds a project, the record ripple's init script wrote, the console
and the exit status of one real run (and of the `help` run that finds the projects when ids were asked for), recorded by
tests/fixtures/gradle_check/capture.py through the real runner. They are the shapes the hand-built records of
test_check_gradle_record.py imitate, and the cases a review reproduced with a green verdict: a test JVM that exits with
status 0 (plain, in a meta-annotated test, in an inherited one, in a static initializer), a project whose `test` task the
build leaves out, two parameterized tests asked together of which one ran nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from gradle_check_support import CAPTURES, capture_names, capture_repo, install, replay

from ripple import checker, gradle_check
from ripple.profile import JAVA


def verdict(monkeypatch, tmp_path, name: str, tests=None) -> dict:
    repo = capture_repo(tmp_path, name)
    install(monkeypatch, replay(name))
    return checker.run_check(repo, tests=tests, timeout=60, profile=JAVA)


def text(result: dict) -> str:
    return " | ".join(result["problems"] + result["failures"] + result.get("notes", []))


def console(name: str) -> str:
    return (CAPTURES / name / "calls" / "0.console").read_text()


def test_a_real_green_run_passes(tmp_path, monkeypatch):
    result = verdict(monkeypatch, tmp_path, "shop_green")
    assert result["passed"] is True and result["counts"]["passed"] == 5 and result["counts"]["classes"] == 3, result


def test_a_real_failing_run_is_listed_and_not_passed(tmp_path, monkeypatch):
    result = verdict(monkeypatch, tmp_path, "shop_failing")
    assert result["passed"] is False and result["failure_count"] == 2 and result["problems"] == [], result
    assert "CartTest.totalIsWrong" in result["failures"][0] and "CartTest.boom" in result["failures"][1]


@pytest.mark.parametrize("name", ["exit_zero", "exit_meta_annotated", "exit_inherited", "exit_static_initializer"])
def test_a_test_that_exited_the_jvm_with_status_zero_is_not_passed_although_gradle_said_build_successful(tmp_path, monkeypatch, name):
    """The shapes a review reproduced with a green verdict (a meta-annotated test, an inherited one) and the exit before any test."""
    assert "BUILD SUCCESSFUL" in console(name)
    result = verdict(monkeypatch, tmp_path, name)
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert "SKIPPED without finishing it" in text(result) and "Gradle Test Executor 1" in text(result)


def test_a_killed_test_jvm_is_not_passed_and_names_the_signal(tmp_path, monkeypatch):
    result = verdict(monkeypatch, tmp_path, "jvm_killed")
    assert result["exit_code"] == 1 and result["passed"] is False, result
    assert "non-zero exit value 137" in text(result)


def test_a_real_suite_of_parameterized_nested_inherited_dynamic_templated_and_skipped_tests_passes(tmp_path, monkeypatch):
    result = verdict(monkeypatch, tmp_path, "real_suite")
    assert result["passed"] is True and result["problems"] == [], result
    assert (result["counts"]["passed"], result["counts"]["failed"], result["counts"]["skipped"], result["counts"]["classes"]) == (26, 0, 10, 15)


def test_two_parameterized_tests_asked_together_one_of_which_ran_nothing_is_not_passed(tmp_path, monkeypatch):
    result = verdict(monkeypatch, tmp_path, "two_parameterized_asked_together", ["com.acme.shop.CartTest2.discounts", "com.acme.shop.CartTest2.refunds"])
    assert result["passed"] is False, result
    assert "CartTest2.refunds: skipped or disabled, so it did not run" in text(result)


def test_a_display_named_test_and_a_parameterized_one_asked_together_are_each_proved(tmp_path, monkeypatch):
    result = verdict(monkeypatch, tmp_path, "display_name_by_id", ["com.acme.shop.CartTest2.discounts", "com.acme.shop.CartTest2.addsItems()"])
    assert result["passed"] is True and result["counts"]["passed"] == 3, result


def test_a_project_whose_test_task_was_left_out_of_the_graph_is_a_problem(tmp_path, monkeypatch):
    graph = gradle_check.read_manifest(CAPTURES / "task_excluded" / "calls" / "0.manifest")
    assert {t.path: t.scheduled for p in graph.projects for t in p.tests} == {":core:test": True, ":web:test": False}
    result = verdict(monkeypatch, tmp_path, "task_excluded")
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert ":web:test was left out of the task graph" in text(result)


def test_a_test_task_with_no_source_is_a_note_beside_a_project_that_ran_tests(tmp_path, monkeypatch):
    events = gradle_check.read_manifest(CAPTURES / "no_source" / "calls" / "0.manifest").events
    assert events[":web:test"].skip == "NO-SOURCE"
    result = verdict(monkeypatch, tmp_path, "no_source")
    assert result["passed"] is True, result
    assert "Gradle found no test classes for :web:test (NO-SOURCE), so no test ran in it" in text(result)


def test_buildsrc_tests_that_ran_are_not_in_the_record_and_do_not_change_the_verdict(tmp_path, monkeypatch):
    """Gradle ran `:buildSrc:test` (the console says so) with the same init script; the init script wrote nothing for it."""
    assert "> Task :buildSrc:test" in console("buildsrc_wired")
    record = (CAPTURES / "buildsrc_wired" / "calls" / "0.manifest").read_text()
    assert "HelperTest" not in record and record.count('"event":"init"') == 1
    result = verdict(monkeypatch, tmp_path, "buildsrc_wired")
    assert result["passed"] is True and result["counts"]["passed"] == 5 and result["counts"]["classes"] == 3, result


def test_tests_the_build_excludes_by_tag_are_a_note_not_a_refusal(tmp_path, monkeypatch):
    result = verdict(monkeypatch, tmp_path, "excluded_by_tag")
    assert result["passed"] is True and result["counts"]["passed"] == 6, result
    assert ":test narrows its tests (excludeTags integration): the tests it leaves out did not run" in result["notes"]


def test_a_captured_record_is_read_with_the_shape_real_gradle_gives_it():
    manifest = gradle_check.read_manifest(CAPTURES / "shop_green" / "calls" / "0.manifest")
    assert manifest.init and manifest.graph and manifest.version == "9.9.1" and not manifest.truncated and manifest.garbled == 0
    [task] = [t for p in manifest.projects for t in p.tests]
    assert task.path == ":test" and task.scheduled and task.enabled
    assert manifest.events[":test"].executed and manifest.events[":test"].skip is None
    assert manifest.events[":compileTestJava"].test is False
    assert {r.cls for r in manifest.results} == {"com.acme.shop.CartTest", "com.acme.shop.CartTest$Receipts", "com.acme.shop.CartTests"}
    assert sorted(r.name for r in manifest.results if r.cls == "com.acme.shop.CartTest") == ["addsItems()", "discounts(int)[1]", "discounts(int)[2]"]
    root = manifest.root(":test")
    assert (root.tests, root.ok, root.failed, root.skipped, root.outcome) == (5, 5, 0, 0, "SUCCESS")
    assert sorted(s.level for s in manifest.suites) == [0, 1, 2, 2, 3, 3]  # run, JVM, two top-level classes, and below CartTest its @Nested class and the parameterized method


def test_in_a_real_exit_the_jvm_suite_and_the_class_end_skipped_and_the_tests_before_it_are_recorded(tmp_path):
    manifest = gradle_check.read_manifest(CAPTURES / "exit_zero" / "calls" / "0.manifest")
    ended = {s.name: s.outcome for s in manifest.suites}
    assert ended["Gradle Test Executor 1"] == "SKIPPED" and ended["com.acme.shop.ZzzExitTest"] == "SKIPPED"
    assert ended["Gradle Test Run :test"] == "SUCCESS" and ended["com.acme.shop.CartTest"] == "SUCCESS"
    assert [r.outcome for r in manifest.results if r.cls == "com.acme.shop.ZzzExitTest"] == ["SUCCESS", "SKIPPED"]


def test_every_capture_has_a_test_here():
    source = Path(__file__).read_text()
    for name in capture_names():
        assert f'"{name}"' in source, f"capture {name} is recorded and no test replays it"


@pytest.mark.parametrize("name", capture_names())
def test_a_capture_replays_to_the_verdict_recorded_with_it(tmp_path, monkeypatch, name):
    """The verdict the runner gave when the run was recorded is the verdict it gives replaying it (a change to the verdict logic shows here)."""
    meta = json.loads((CAPTURES / name / "meta.json").read_text())
    result = verdict(monkeypatch, tmp_path, name, meta["request"] or None)
    assert result["passed"] == meta["recorded_verdict"]["passed"]
    assert result["problems"] == meta["recorded_verdict"]["problems"]
    assert result["summary"] == meta["recorded_verdict"]["summary"]
