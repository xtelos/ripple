"""The verdict `check` gives for Java on Gradle, from the record of Gradle's own test listener, against a stand-in for Gradle.

No JDK or Gradle needed: `Fake` writes what the init script writes (see tests/gradle_check_support.py), shaped after the real
runs recorded in tests/fixtures/gradle_check/captures and the live tests. What these tests pin is the contract of
ripple/gradle_check.py: a pass needs the record to prove tests ran and none failed, and Gradle's exit to agree; the verdict
never reads a test source; and anything the record cannot prove is not a pass.
"""

from __future__ import annotations

import json

import pytest
from gradle_check_support import (
    OK_CONSOLE,
    SHOP,
    case_line,
    graph,
    init_line,
    install,
    lines,
    listener,
    multi,
    project,
    record,
    run_with,
    suite_line,
    task_line,
)

from ripple import checker, gradle_check
from ripple import java_check as jc
from ripple.profile import JAVA

CART = "src/test/java/com/acme/shop/CartTest.java"


def text(result: dict) -> str:
    return " | ".join(result["problems"] + result["failures"] + result.get("notes", []))


def verdict(monkeypatch, tmp_path, rows, **kwargs) -> dict:
    return run_with(monkeypatch, tmp_path, rows, **kwargs)


# ---- a pass, and what it is made of -----------------------------------------------------------------------


def test_a_whole_clean_record_with_exit_zero_passes_with_its_counts_from_the_record(monkeypatch, tmp_path):
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo))
    assert result["passed"] is True and result["problems"] == [] and result["exit_code"] == 0, result
    assert result["summary"] == "5 passed, 0 failed, 0 skipped in 3 test classes"
    assert result["counts"] == {"passed": 5, "failed": 0, "errored": 0, "skipped": 0, "classes": 3, "flaky": 0}


def test_the_pass_does_not_read_a_test_source_so_what_gradle_ran_is_what_counts(monkeypatch, tmp_path):
    """Gradle decides which tests exist. Classes the sources never declare, and methods they declare that Gradle did not report, change nothing."""

    def boom(*args, **kwargs):
        raise AssertionError("a test source was read to reach a verdict")

    for name in ("read_java_file", "read_java", "read_regular"):
        monkeypatch.setattr(jc, name, boom)
    classes = {"com.elsewhere.GeneratedTest": [("dynamic()[1]", "SUCCESS"), ("dynamic()[2]", "SUCCESS")], "com.elsewhere.OtherTest": [("viaInheritance()", "SUCCESS")]}
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo, classes))
    assert result["passed"] is True and result["counts"]["passed"] == 3 and result["counts"]["classes"] == 2, result
    assert (tmp_path / "repo" / CART).exists()  # the shop sources are there, and hold test methods this record never mentions


@pytest.mark.parametrize(
    "name",
    ["a()", "discounts(int)[1]", "[1] 1", "repeated(RepetitionInfo)[3]", "dyn()[2]", "adds items to the cart", "viaTemplate(String, int)[7]"],
)
def test_any_name_gradle_gives_a_test_is_a_test(monkeypatch, tmp_path, name):
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo, {"com.acme.AnyTest": [(name, "SUCCESS")]}))
    assert result["passed"] is True and result["counts"]["passed"] == 1, result


def test_a_skipped_test_among_passing_ones_is_a_note_and_a_pass(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("a()", "SUCCESS"), ("off()", "SKIPPED")]}
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo, classes))
    assert result["passed"] is True and "1 test skipped or disabled, so not run" in text(result), result


def test_the_failing_tests_are_listed_as_file_class_method_and_the_run_is_not_a_pass(monkeypatch, tmp_path):
    rows = lambda repo: [
        init_line(), graph(repo),
        case_line(":test", "com.acme.shop.CartTest", "totalIsWrong()", "FAILURE", error="expected: <1> but was: <2>"),
        case_line(":test", "com.acme.shop.CartTest$Receipts", "boom()", "FAILURE", error="java.lang.IllegalStateException: bad"),
        case_line(":test", "com.acme.shop.CartTest", "ok()"),
        suite_line(":test", "com.acme.shop.CartTest", 2, "FAILURE", 2, 1, 1, cls="com.acme.shop.CartTest"),
        suite_line(":test", "com.acme.shop.CartTest$Receipts", 3, "FAILURE", 1, 0, 1, cls="com.acme.shop.CartTest$Receipts"),
        suite_line(":test", "Gradle Test Executor 1", 1, "FAILURE", 3, 1, 2),
        suite_line(":test", "Gradle Test Run :test", 0, "FAILURE", 3, 1, 2),
        task_line(":test", failure="There were failing tests"),
    ]
    result = verdict(monkeypatch, tmp_path, rows, exit_code=1)
    assert result["passed"] is False and result["failure_count"] == 2 and result["problems"] == [], result
    assert result["failures"][0] == f"FAILED {CART}::CartTest.totalIsWrong(): expected: <1> but was: <2>"
    assert result["failures"][1].startswith(f"FAILED {CART}::CartTest.Receipts.boom()")
    assert result["counts"]["failed"] == 2 and result["exit_code"] == 1


# ---- a JVM that stopped: never a pass --------------------------------------------------------------------


def exited(repo, **options):
    """A run where the JVM's own suite and the class it stopped in are SKIPPED, and the test that exited is a skip with a time."""
    classes = {
        "com.acme.shop.CartTest": [("addsItems()", "SUCCESS")],
        "com.acme.shop.ZzzExitTest": [("first()", "SUCCESS"), ("quits()", "SKIPPED")],
    }
    return record(repo, classes, executor="SKIPPED", class_result={"com.acme.shop.ZzzExitTest": "SKIPPED"}, **options)


def test_a_jvm_that_exited_with_status_zero_is_not_a_pass_although_gradle_said_build_successful(monkeypatch, tmp_path):
    result = verdict(monkeypatch, tmp_path, exited, exit_code=0, console="> Task :test\n\nBUILD SUCCESSFUL in 9s\n")
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert "ended 2 suites SKIPPED without finishing it (com.acme.shop.ZzzExitTest, Gradle Test Executor 1)" in text(result)


@pytest.mark.parametrize("which", ["executor", "class"])
def test_either_suite_ending_skipped_alone_is_enough_to_refuse(monkeypatch, tmp_path, which):
    """An exit in a static initializer or @AfterAll leaves every test passed and only the suites SKIPPED (measured on Gradle 9.9.1)."""
    classes = {"com.acme.shop.AaaExitTest": [("a()", "SUCCESS")]}
    options = {"executor": "SKIPPED"} if which == "executor" else {"class_result": {"com.acme.shop.AaaExitTest": "SKIPPED"}}
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo, classes, **options))
    assert result["passed"] is False and "SKIPPED without finishing it" in text(result), result


def test_a_disabled_parameterized_method_ends_its_own_container_skipped_and_that_is_an_ordinary_skip(monkeypatch, tmp_path):
    """Measured: `@Disabled @ParameterizedTest` gives a SKIPPED suite named after the method, with no test under it, in a JVM Gradle ended normally."""
    rows = lambda repo: [
        init_line(), graph(repo),
        case_line(":test", "com.acme.shop.SkipShapesTest", "plain()"),
        suite_line(":test", "offParam(int)", 3, "SKIPPED", 0, 0, cls="com.acme.shop.SkipShapesTest"),
        suite_line(":test", "com.acme.shop.SkipShapesTest", 2, "SUCCESS", 1, 1, cls="com.acme.shop.SkipShapesTest"),
        suite_line(":test", "Gradle Test Executor 1", 1, "SUCCESS", 1, 1),
        suite_line(":test", "Gradle Test Run :test", 0, "SUCCESS", 1, 1),
        task_line(":test"),
    ]
    assert verdict(monkeypatch, tmp_path, rows)["passed"] is True


def test_a_suite_skipped_by_an_assumption_is_not_an_exit(monkeypatch, tmp_path):
    rows = lambda repo: [
        init_line(), graph(repo),
        case_line(":test", "com.acme.shop.AssumeTest", "a()", "SKIPPED", ms=1),
        case_line(":test", "com.acme.shop.OkTest", "b()"),
        suite_line(":test", "com.acme.shop.AssumeTest", 2, "SKIPPED", 1, 0, 0, 1, cls="com.acme.shop.AssumeTest", assumed=True),
        suite_line(":test", "com.acme.shop.OkTest", 2, "SUCCESS", 1, 1, cls="com.acme.shop.OkTest"),
        suite_line(":test", "Gradle Test Executor 1", 1, "SUCCESS", 2, 1, 0, 1),
        suite_line(":test", "Gradle Test Run :test", 0, "SUCCESS", 2, 1, 0, 1),
        task_line(":test"),
    ]
    assert verdict(monkeypatch, tmp_path, rows)["passed"] is True


def test_a_skipped_test_with_a_time_in_suites_that_ended_normally_is_not_read_as_an_exit(monkeypatch, tmp_path):
    """Measured: a `@Disabled` test took 1 ms, and an assumption in @BeforeAll skipped a class's tests with 0 and 1 ms and no assumption on them."""
    classes = {"com.acme.shop.CartTest": [("a()", "SUCCESS")]}
    rows = lambda repo: [
        init_line(), graph(repo),
        case_line(":test", "com.acme.shop.OffTest", "off()", "SKIPPED", ms=1),
        *listener(":test", classes)[:-2],
        suite_line(":test", "com.acme.shop.OffTest", 2, "SUCCESS", 1, 0, 0, 1, cls="com.acme.shop.OffTest"),
        suite_line(":test", "Gradle Test Executor 1", 1, "SUCCESS", 2, 1, 0, 1),
        suite_line(":test", "Gradle Test Run :test", 0, "SUCCESS", 2, 1, 0, 1),
        task_line(":test"),
    ]
    assert verdict(monkeypatch, tmp_path, rows)["passed"] is True


def test_a_jvm_that_was_killed_is_not_a_pass_and_says_what_gradle_said(monkeypatch, tmp_path):
    msg = "Process 'Gradle Test Executor 1' finished with non-zero exit value 137 (this value may indicate that the process was terminated with the SIGKILL signal)"
    rows = lambda repo: [
        init_line(), graph(repo),
        case_line(":test", "com.acme.shop.ZzzKillTest", "killed()", "SKIPPED", ms=48),
        suite_line(":test", "com.acme.shop.ZzzKillTest", 2, "SKIPPED", 1, 0, 0, 1, cls="com.acme.shop.ZzzKillTest"),
        suite_line(":test", "Gradle Test Executor 1", 1, "SKIPPED", 1, 0, 0, 1),
        suite_line(":test", "Gradle Test Run :test", 0, "FAILURE", 1, 0, 0, 1, error=msg),
        task_line(":test", failure=msg),
    ]
    result = verdict(monkeypatch, tmp_path, rows, exit_code=1, console=f"> Task :test FAILED\n* What went wrong:\nExecution failed for task ':test'.\n> {msg}\n")
    assert result["passed"] is False and result["exit_code"] == 1, result
    assert "finished with non-zero exit value 137" in text(result) and "SKIPPED without finishing it" in text(result)


def test_a_jvm_that_was_killed_before_it_wrote_a_closing_record_is_not_a_pass(monkeypatch, tmp_path):
    rows = lambda repo: [init_line(), graph(repo), case_line(":test", "com.acme.shop.CartTest", "a()")]
    result = verdict(monkeypatch, tmp_path, rows, exit_code=137, console="Killed\n")
    assert result["passed"] is False and result["exit_code"] == 137, result
    assert "no closing record" in text(result) or "did not run" in text(result)


# ---- a record that cannot be trusted: never a pass --------------------------------------------------------


def test_a_record_cut_off_in_the_middle_of_a_line_is_not_a_pass(monkeypatch, tmp_path):
    # the Fake appends whole lines, so write the bytes ourselves: everything, then half of a last line
    repo = project(tmp_path, "shop")

    def script(argv, cwd, manifest):
        body = "\n".join(record(cwd)) + "\n" + '{"event":"test","task":":test","cls":"com.acme.shop.CartTest","na'
        manifest.write_text(body)
        return 0, OK_CONSOLE

    install(monkeypatch, script)
    result = checker.run_check(repo, tests=[], timeout=60, profile=JAVA)
    assert result["passed"] is False and "cut off in the middle of a line" in text(result), result


@pytest.mark.parametrize("keep_outcome,words", [(False, ":test did not run"), (True, ":test has no closing record")])
def test_a_record_that_ends_where_a_line_ends_but_lost_its_closing_lines_is_not_a_pass(monkeypatch, tmp_path, keep_outcome, words):
    """The JVM's suite and the run's suite are the last the listener writes; with the task's outcome or without it, no closing record is no pass."""
    rows = lambda repo: record(repo)[:-3] + ([task_line(":test")] if keep_outcome else [])
    result = verdict(monkeypatch, tmp_path, rows)
    assert result["passed"] is False and words in text(result), result


def test_a_record_that_lost_a_test_line_does_not_add_up_to_gradles_own_totals(monkeypatch, tmp_path):
    def rows(repo):
        every = record(repo)
        drop = next(i for i, row in enumerate(every) if '"event": "test"' in row and "discounts(int)[2]" in row)
        return every[:drop] + every[drop + 1 :]

    result = verdict(monkeypatch, tmp_path, rows)
    assert result["passed"] is False and "the record holds 4 tests" in text(result) and "Gradle counted 5" in text(result), result


def test_an_empty_record_is_not_a_pass(monkeypatch, tmp_path):
    result = verdict(monkeypatch, tmp_path, [])
    assert result["passed"] is False and "did not report which tasks it would run" in text(result), result
    assert verdict(monkeypatch, tmp_path / "again", [], raw="")["passed"] is False


def test_a_record_with_no_task_graph_is_not_a_pass_however_green_its_tests(monkeypatch, tmp_path):
    rows = lambda repo: [init_line(), *listener(":test", SHOP), task_line(":test")]
    result = verdict(monkeypatch, tmp_path, rows)
    assert result["passed"] is False and "did not report which tasks it would run" in text(result), result


def test_a_record_with_no_init_line_is_not_a_pass(monkeypatch, tmp_path):
    rows = lambda repo: [graph(repo), *listener(":test", SHOP), task_line(":test")]
    assert verdict(monkeypatch, tmp_path, rows)["passed"] is False


def test_garbage_lines_in_the_record_are_counted_and_refused(monkeypatch, tmp_path):
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo)[:3] + ["not json {", "[1, 2]"] + record(repo)[3:])
    assert result["passed"] is False and "2 lines of the record of the test run could not be read" in text(result), result


def test_a_record_over_the_size_ripple_reads_is_not_a_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(gradle_check, "MAX_RECORD_BYTES", 600)
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo))
    assert result["passed"] is False and "over the size ripple reads" in text(result), result


def test_a_record_that_disagrees_with_a_nonzero_exit_is_not_a_pass(monkeypatch, tmp_path):
    """Every test passed in the record, and Gradle exited 1: something else failed the build (a later task, a compile error in another project)."""
    console = "> Task :test\n* What went wrong:\nExecution failed for task ':jacocoTestCoverageVerification'.\n> Rule violated\n\nBUILD FAILED in 4s\n"
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo), exit_code=1, console=console)
    assert result["passed"] is False and result["exit_code"] == 1, result
    assert "gradle exited 1 but its record shows no failing test" in text(result)


def test_a_record_with_a_failing_test_and_exit_zero_is_a_failure_with_the_failure_listed(monkeypatch, tmp_path):
    """`ignoreFailures`: Gradle says BUILD SUCCESSFUL, and the listener saw the test fail."""
    classes = {"com.acme.shop.CartTest": [("a()", "SUCCESS"), ("b()", "FAILURE")]}
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo, classes), exit_code=0)
    assert result["passed"] is False and result["failure_count"] == 1, result
    assert "gradle exited 0 although tests failed" in text(result)


def test_a_run_killed_for_its_time_is_not_a_pass_even_with_a_whole_record(monkeypatch, tmp_path):
    repo = project(tmp_path, "shop")

    def script(argv, cwd, manifest):
        lines(manifest, *record(cwd))
        return 0, OK_CONSOLE, True

    install(monkeypatch, script)
    result = checker.run_check(repo, tests=[], timeout=60, profile=JAVA)
    assert result["passed"] is False and result["timed_out"] is True and "timed out after 60s" in text(result), result


def test_a_build_that_never_reached_its_task_graph_says_why(monkeypatch, tmp_path):
    console = "FAILURE: Build failed with an exception.\n* What went wrong:\nA problem occurred evaluating root project 'shop'.\n> boom\n"
    result = verdict(monkeypatch, tmp_path, [init_line()], exit_code=1, console=console)
    assert result["passed"] is False and "evaluating root project" in text(result), result


def test_a_compile_error_is_named_and_is_not_a_pass(monkeypatch, tmp_path):
    console = "src/test/java/com/acme/shop/CartTest.java:12: error: cannot find symbol\n"
    rows = lambda repo: [
        init_line(), graph(repo),
        task_line(":compileTestJava", failure="Execution failed for task ':compileTestJava'. <- Compilation failed", test=False),
    ]
    result = verdict(monkeypatch, tmp_path, rows, exit_code=1, console=console)
    assert result["passed"] is False and "compilation failed: CartTest.java:12" in text(result), result
    assert "did not run" in text(result)


# ---- Gradle's own words, a failure of ripple's init script, and the Gradle that runs ---------------------------------


def test_a_failure_of_the_init_script_is_said_as_that_and_never_as_a_dead_jvm(monkeypatch, tmp_path):
    """Gradle 8 has no TestResult.assumptionFailure: the listener used to throw on every test and the problem blamed the test JVM."""
    console = "> Task :test FAILED\n* What went wrong:\nExecution failed for task ':test'.\n> No such property: assumptionFailure for class: DefaultTestResult\n"
    rows = lambda repo: [
        init_line(), graph(repo),
        json.dumps({"event": "listener_error", "message": "No such property: assumptionFailure for class: DefaultTestResult"}),
        task_line(":test", failure="Execution failed for task ':test'. <- No such property: assumptionFailure"),
    ]
    result = verdict(monkeypatch, tmp_path, rows, exit_code=1, console=console)
    assert result["passed"] is False, result
    assert "ripple's own init script failed inside Gradle (No such property: assumptionFailure" in text(result)
    assert "says nothing about the tests" in text(result) and "test JVM" not in " ".join(result["problems"][:1])
    assert "No such property: assumptionFailure" in " ".join(result["problems"][1:]), "Gradle's own text reaches the problems"


def test_gradles_own_what_went_wrong_reaches_the_problems_when_a_test_task_is_the_one_that_failed(monkeypatch, tmp_path):
    console = "> Task :test FAILED\n* What went wrong:\nExecution failed for task ':test'.\n> Could not start Gradle Test Executor 1: out of memory\n"
    rows = lambda repo: [init_line(), graph(repo), task_line(":test", failure="Execution failed for task ':test'. <- Could not start Gradle Test Executor 1: out of memory")]
    result = verdict(monkeypatch, tmp_path, rows, exit_code=1, console=console)
    assert result["passed"] is False and "Could not start Gradle Test Executor 1: out of memory" in " ".join(result["problems"]), result


@pytest.mark.parametrize("version", ["7.6.4", "8.9", "8.0.2", "6.9"])
def test_a_gradle_older_than_the_oldest_measured_is_refused_up_front_naming_both(monkeypatch, tmp_path, version):
    repo = project(tmp_path, "shop")
    fake = install(monkeypatch, lambda argv, cwd, manifest: (_ for _ in ()).throw(AssertionError("no build may run on a Gradle that is refused")), version=version)
    with pytest.raises(ValueError, match=rf"Gradle {version} is older than 8\.10, the oldest Gradle ripple has measured"):
        checker.run_check(repo, tests=[], timeout=60, profile=JAVA)
    assert [c[-1] for c in fake.calls] == ["--version"]


@pytest.mark.parametrize("version", ["8.10", "8.10.2", "8.14.3", "9.9.1", "9.0.0-rc-1"])
def test_a_gradle_of_the_measured_range_is_run(monkeypatch, tmp_path, version):
    repo = project(tmp_path, "shop")
    install(monkeypatch, lambda argv, cwd, manifest: (lines(manifest, *record(cwd)), (0, OK_CONSOLE))[1], version=version)
    assert checker.run_check(repo, tests=[], timeout=60, profile=JAVA)["passed"] is True


def test_a_gradle_whose_version_cannot_be_read_is_refused(monkeypatch, tmp_path):
    repo = project(tmp_path, "shop")
    install(monkeypatch, lambda argv, cwd, manifest: (0, ""), version="not a version")
    with pytest.raises(ValueError, match="could not read the Gradle version"):
        checker.run_check(repo, tests=[], timeout=60, profile=JAVA)


# ---- tasks that did not run ------------------------------------------------------------------------------


@pytest.mark.parametrize("skip", ["UP-TO-DATE", "FROM-CACHE", "SKIPPED", "SOMETHING-NEW"])
def test_a_test_task_gradle_did_not_execute_is_never_a_pass_whatever_a_record_of_it_says(monkeypatch, tmp_path, skip):
    rows = lambda repo: [init_line(), graph(repo), *listener(":test", SHOP), task_line(":test", skip=skip, executed=False)]
    result = verdict(monkeypatch, tmp_path, rows)
    assert result["passed"] is False and ":test" in text(result) and "Gradle" in text(result), result


def test_a_test_task_with_no_sources_is_a_note_when_another_project_ran_tests(monkeypatch, tmp_path):
    repo = multi(tmp_path)

    def script(argv, cwd, manifest):
        both = {":core": "core", ":web": "web"}
        lines(manifest, init_line(), graph(cwd, both), *listener(":core:test", {"com.acme.core.CoreTest": [("works()", "SUCCESS")]}),
              task_line(":core:test"), task_line(":web:test", skip="NO-SOURCE", executed=False))
        return 0, OK_CONSOLE

    install(monkeypatch, script)
    result = checker.run_check(repo, tests=[], timeout=60, profile=JAVA)
    assert result["passed"] is True and "Gradle found no test classes for :web:test (NO-SOURCE), so no test ran in it" in text(result), result


def test_a_no_source_task_still_reports_what_the_build_narrows(monkeypatch, tmp_path):
    """`test { exclude '**/*' }` makes Gradle say NO-SOURCE although the module has test sources: the note says what Gradle said, and what was excluded."""
    repo = multi(tmp_path)

    def script(argv, cwd, manifest):
        data = json.loads(graph(cwd, {":core": "core"}))
        data["projects"].append(json.loads(graph(cwd, {":web": "web"}, exclude=["**/*"]))["projects"][0])
        lines(manifest, init_line(), json.dumps(data), *listener(":core:test", {"com.acme.core.CoreTest": [("works()", "SUCCESS")]}), task_line(":core:test"), task_line(":web:test", skip="NO-SOURCE", executed=False))
        return 0, OK_CONSOLE

    install(monkeypatch, script)
    result = checker.run_check(repo, tests=[], timeout=60, profile=JAVA)
    assert result["passed"] is True, result
    assert "Gradle found no test classes for :web:test (NO-SOURCE), so no test ran in it" in result["notes"]
    assert ":web:test narrows its tests (excludes **/*): the tests it leaves out did not run" in result["notes"]
    assert "no test sources" not in text(result)


def test_a_run_where_every_test_task_has_no_sources_is_not_a_pass(monkeypatch, tmp_path):
    rows = lambda repo: [init_line(), graph(repo), task_line(":test", skip="NO-SOURCE", executed=False)]
    result = verdict(monkeypatch, tmp_path, rows)
    assert result["passed"] is False and "no test ran" in text(result), result


def core_runs_web_does_not(cwd) -> str:
    """A graph of the two projects of `multi` in which :core:test is scheduled and :web:test exists but is not."""
    data = json.loads(graph(cwd, {":core": "core"}))
    data["projects"].append(json.loads(graph(cwd, {":web": "web"}, scheduled=False))["projects"][0])
    return json.dumps(data)


def test_a_test_task_the_build_left_out_of_the_graph_is_a_problem_naming_it(monkeypatch, tmp_path):
    repo = multi(tmp_path)

    def script(argv, cwd, manifest):
        lines(manifest, init_line(), core_runs_web_does_not(cwd), *listener(":core:test", {"com.acme.core.CoreTest": [("works()", "SUCCESS")]}), task_line(":core:test"))
        return 0, OK_CONSOLE

    install(monkeypatch, script)
    result = checker.run_check(repo, tests=[], timeout=60, profile=JAVA)
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert ":web:test was left out of the task graph" in text(result)


def test_no_test_task_in_the_graph_is_not_a_pass(monkeypatch, tmp_path):
    result = verdict(monkeypatch, tmp_path, lambda repo: [init_line(), graph(repo, scheduled=False)])
    assert result["passed"] is False and "no `test` task was scheduled" in text(result), result


def test_a_scheduled_test_task_with_no_outcome_record_did_not_run(monkeypatch, tmp_path):
    result = verdict(monkeypatch, tmp_path, lambda repo: [init_line(), graph(repo), *listener(":test", SHOP)])
    assert result["passed"] is False and ":test did not run" in text(result), result


@pytest.mark.parametrize("enabled,why", [(False, "enabled = false"), (True, "an onlyIf condition that is false")])
def test_a_test_task_the_build_disables_or_skips_is_a_note_naming_the_task_and_the_reason(monkeypatch, tmp_path, enabled, why):
    """A JMH, docs or samples module with `test { enabled = false }` is ordinary, and `exclude '**/*'` already passed with a note."""
    repo = multi(tmp_path)

    def script(argv, cwd, manifest):
        data = json.loads(graph(cwd, {":core": "core"}))
        data["projects"].append(json.loads(graph(cwd, {":web": "web"}, enabled=enabled))["projects"][0])
        lines(manifest, init_line(), json.dumps(data), *listener(":core:test", {"com.acme.core.CoreTest": [("works()", "SUCCESS")]}), task_line(":core:test"), task_line(":web:test", skip="SKIPPED", executed=False))
        return 0, OK_CONSOLE

    install(monkeypatch, script)
    result = checker.run_check(repo, tests=[], timeout=60, profile=JAVA)
    assert result["passed"] is True and result["problems"] == [], result
    assert f":web:test was skipped by the build (Gradle said SKIPPED: {why}), so its tests did not run" in result["notes"]


def test_a_build_whose_every_test_task_is_disabled_is_still_not_a_pass(monkeypatch, tmp_path):
    rows = lambda repo: [init_line(), graph(repo, enabled=False), task_line(":test", skip="SKIPPED", executed=False)]
    result = verdict(monkeypatch, tmp_path, rows)
    assert result["passed"] is False and "no tests ran" in text(result) and ":test was skipped by the build" in text(result), result


def test_a_disabled_test_task_asked_for_by_name_is_a_problem(monkeypatch, tmp_path):
    repo = project(tmp_path, "shop")

    def script(argv, cwd, manifest):
        if "help" in argv:
            lines(manifest, init_line(), graph(cwd, scheduled=False))
            return 0, ""
        lines(manifest, init_line(), graph(cwd, enabled=False), task_line(":test", skip="SKIPPED", executed=False))
        return 0, OK_CONSOLE

    install(monkeypatch, script)
    result = checker.run_check(repo, tests=["com.acme.shop.CartTest"], timeout=60, profile=JAVA)
    assert result["passed"] is False and ":test was skipped by Gradle (SKIPPED)" in text(result) and "asked for" in text(result), result


def test_every_test_skipped_is_not_a_pass(monkeypatch, tmp_path):
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo, {"com.acme.shop.OffTest": [("a()", "SKIPPED"), ("b()", "SKIPPED")]}))
    assert result["passed"] is False and "every test that ran was skipped" in text(result), result


def test_a_task_that_ran_no_test_at_all_is_not_a_pass(monkeypatch, tmp_path):
    result = verdict(monkeypatch, tmp_path, lambda repo: record(repo, {}))
    assert result["passed"] is False and ":test executed and ran no test" in text(result), result


def test_a_test_task_that_executed_and_ran_zero_tests_is_a_problem_even_when_another_project_ran_tests(monkeypatch, tmp_path):
    """`failOnNoDiscoveredTests = false` (the default before Gradle 9) with no `useJUnitPlatform()`: web's task executes, finds nothing, and its tests never run."""
    repo = multi(tmp_path)

    def script(argv, cwd, manifest):
        both = {":core": "core", ":web": "web"}
        lines(
            manifest, init_line(), graph(cwd, both),
            *listener(":core:test", {"com.acme.core.CoreTest": [("works()", "SUCCESS")]}), task_line(":core:test"),
            *listener(":web:test", {}), task_line(":web:test"),
        )
        return 0, OK_CONSOLE

    install(monkeypatch, script)
    result = checker.run_check(repo, tests=[], timeout=60, profile=JAVA)
    assert result["exit_code"] == 0 and result["passed"] is False, result
    assert ":web:test executed and ran no test" in text(result) and ":core:test" not in " ".join(result["problems"])


def test_a_narrowed_test_task_that_ran_zero_tests_is_a_note_not_a_problem(monkeypatch, tmp_path):
    rows = lambda repo: [
        init_line(), graph(repo, exclude=["**/*Nope*"]), *listener(":test", {}), task_line(":test"),
    ]
    result = verdict(monkeypatch, tmp_path, rows)
    assert ":test executed and ran no test" in " ".join(result["notes"]) and "narrows" in text(result), result
    assert result["passed"] is False  # nothing ran anywhere, so still no pass


def test_a_second_test_task_the_build_wired_in_counts_like_the_first(monkeypatch, tmp_path):
    """`test.finalizedBy integrationTest`: it is in the graph, so its failure fails the verdict."""
    extra = {"name": "integrationTest", "path": ":integrationTest", "enabled": True, "ignoreFailures": False, "scheduled": True, "classesDirs": []}
    rows = lambda repo: [
        init_line(), graph(repo, extra_tasks=[extra]), *listener(":test", SHOP), task_line(":test"),
        *listener(":integrationTest", {"com.acme.shop.ItTest": [("slow()", "FAILURE")]}), task_line(":integrationTest", failure="There were failing tests"),
    ]
    result = verdict(monkeypatch, tmp_path, rows, exit_code=1)
    assert result["passed"] is False and any("ItTest.slow()" in f for f in result["failures"]), result


def test_a_test_task_that_test_does_not_run_is_a_note_not_a_refusal(monkeypatch, tmp_path):
    extra = {"name": "integrationTest", "path": ":integrationTest", "enabled": True, "ignoreFailures": False, "scheduled": False, "classesDirs": []}
    result = verdict(monkeypatch, tmp_path, lambda repo: [init_line(), graph(repo, extra_tasks=[extra]), *listener(":test", SHOP), task_line(":test")])
    assert result["passed"] is True and ":integrationTest is a test task that `test` does not run" in text(result), result


# ---- what the build narrows: a note, never a refusal -------------------------------------------------------


@pytest.mark.parametrize(
    "narrowing,words",
    [({"tagsExcluded": ["integration"]}, "excludeTags integration"), ({"exclude": ["**/*IT.class"]}, "excludes **/*IT.class"), ({"filterIncludes": ["*Fast*"]}, "a test filter *Fast*")],
)
def test_tests_the_builds_own_test_task_deselects_are_a_note_and_the_rest_still_passes(monkeypatch, tmp_path, narrowing, words):
    result = verdict(monkeypatch, tmp_path, lambda repo: [init_line(), graph(repo, **narrowing), *listener(":test", SHOP), task_line(":test")])
    assert result["passed"] is True, result
    assert f":test narrows its tests ({words}): the tests it leaves out did not run" in result["notes"]


def test_ignore_failures_is_a_note_and_a_failing_test_there_still_fails(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("a()", "FAILURE")]}
    rows = lambda repo: [init_line(), graph(repo, ignoreFailures=True), *listener(":test", classes), task_line(":test")]
    result = verdict(monkeypatch, tmp_path, rows)
    assert result["passed"] is False and ":test has ignoreFailures set" in text(result), result


# ---- the environment around the build ----------------------------------------------------------------------


def test_init_scripts_of_the_gradle_home_and_jvm_options_are_named_not_read(monkeypatch, tmp_path):
    home = tmp_path / "guh"
    (home / "init.d").mkdir(parents=True)
    (home / "init.d" / "ci.gradle").write_text("// excludes tests\n")
    monkeypatch.setenv("GRADLE_USER_HOME", str(home))
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Dsomething=1")
    result = verdict(monkeypatch, tmp_path / "r", lambda repo: record(repo))
    assert result["passed"] is True, result
    assert any("init.d holds init scripts (ci.gradle)" in n for n in result["notes"])
    assert any("$JAVA_TOOL_OPTIONS is set" in n for n in result["notes"])


# ---- what was asked for ---------------------------------------------------------------------------------------


def asked(monkeypatch, tmp_path, tests: list[str], rows, exit_code: int = 0, repo=None) -> tuple[dict, list[list[str]]]:
    """check with ids against a fake whose `help` run reports the projects and whose run writes `rows(repo)`."""
    repo = repo or project(tmp_path, "shop")

    def script(argv, cwd, manifest):
        if "help" in argv:
            lines(manifest, init_line(), graph(cwd, scheduled=False))
            return 0, ""
        lines(manifest, *rows(cwd, argv))
        return exit_code, OK_CONSOLE

    fake = install(monkeypatch, script)
    return checker.run_check(repo, tests=tests, timeout=60, profile=JAVA), fake.calls


def asked_rows(classes, patterns=None, **options):
    def rows(repo, argv):
        shown = patterns if patterns is not None else [argv[i + 1] for i, a in enumerate(argv) if a == "--tests"]
        return [init_line(), graph(repo, commandLine=shown), *listener(":test", classes, **options), task_line(":test")]

    return rows


def test_a_method_asked_for_is_proved_by_a_test_gradle_names_after_that_method(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("addsItems()", "SUCCESS")]}
    result, calls = asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest.addsItems()"], asked_rows(classes))
    assert result["passed"] is True, result
    assert calls[-1][-3:] == [":test", "--tests", "com.acme.shop.CartTest.addsItems"]


def test_a_method_with_a_display_name_is_still_found_by_its_method_name(monkeypatch, tmp_path):
    """Gradle's name stays `addsItems()` when the test has a @DisplayName: the text is in the display name, which is not what is matched (measured)."""
    classes = {"com.acme.shop.CartTest": [("addsItems()", "SUCCESS")]}
    result, _ = asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest.addsItems()"], asked_rows(classes))
    assert result["passed"] is True and result["counts"]["passed"] == 1, result


def test_a_parameterized_method_asked_for_is_proved_by_one_of_its_invocations(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("discounts(int)[1]", "SUCCESS"), ("discounts(int)[2]", "SUCCESS")]}
    result, _ = asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest.discounts(int)"], asked_rows(classes))
    assert result["passed"] is True and result["counts"]["passed"] == 2, result


def test_two_parameterized_methods_asked_together_each_need_an_invocation_that_ran(monkeypatch, tmp_path):
    """The shape that read as a pass when `refunds` ran none of its invocations: both are tied to their own method by Gradle's names."""
    classes = {"com.acme.shop.CartTest": [("discounts(int)[1]", "SUCCESS"), ("refunds(int)[1]", "SKIPPED"), ("refunds(int)[2]", "SKIPPED")]}
    result, _ = asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest.discounts(int)", "com.acme.shop.CartTest.addsItems()"], asked_rows(classes))
    assert result["passed"] is False and "no test ran" in text(result), result
    both = ["com.acme.shop.CartTest.discounts(int)", "com.acme.shop.CartTest.addsItems()"]
    classes["com.acme.shop.CartTest"] = [("discounts(int)[1]", "SUCCESS"), ("addsItems()", "SKIPPED")]
    result, _ = asked(monkeypatch, tmp_path / "again", both, asked_rows(classes))
    assert result["passed"] is False and "skipped or disabled, so it did not run" in text(result), result


def test_a_method_asked_for_that_only_other_tests_of_its_class_ran_is_not_proved(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("somethingElse()", "SUCCESS")]}
    result, _ = asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest.addsItems()"], asked_rows(classes))
    assert result["passed"] is False and "no test ran (Gradle reported none, though other tests of the class ran)" in text(result), result


def test_a_class_asked_for_is_proved_by_a_test_of_it_or_of_its_nested_classes_not_by_another_class(monkeypatch, tmp_path):
    ok = {"com.acme.shop.CartTest": [("a()", "SUCCESS")]}
    assert asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest"], asked_rows(ok))[0]["passed"] is True
    nested = {"com.acme.shop.CartTest$Receipts": [("a()", "SUCCESS")]}
    assert asked(monkeypatch, tmp_path / "n", ["com.acme.shop.CartTest"], asked_rows(nested))[0]["passed"] is True
    other = {"com.acme.shop.CartTests": [("a()", "SUCCESS")]}
    result = asked(monkeypatch, tmp_path / "o", ["com.acme.shop.CartTest"], asked_rows(other))[0]
    assert result["passed"] is False and "no test ran" in text(result), result


def test_a_class_asked_for_whose_tests_were_all_skipped_is_not_a_pass(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("a()", "SKIPPED")], "com.acme.shop.CartTests": [("b()", "SUCCESS")]}
    result, _ = asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest"], asked_rows(classes))
    assert result["passed"] is False and "CartTest: skipped or disabled, so it did not run" in text(result), result


def test_the_filter_gradle_applied_must_be_the_one_asked_for(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("addsItems()", "SUCCESS")]}
    result, _ = asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest.addsItems()"], asked_rows(classes, patterns=["com.acme.shop.Other"]))
    assert result["passed"] is False and "Gradle applied the filter ['com.acme.shop.Other'], not the ['com.acme.shop.CartTest.addsItems'] asked for" in text(result), result


def test_an_overload_asked_for_by_its_types_must_be_the_one_that_ran(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("add(int)", "SUCCESS")]}
    result, _ = asked(monkeypatch, tmp_path, ["com.acme.shop.CartTest.addsItems(int)"], asked_rows(classes, patterns=["com.acme.shop.CartTest.addsItems"]))
    assert "addsItems" in text(result) and result["passed"] is False


def test_the_jvm_that_exited_in_a_run_for_ids_is_not_a_pass_either(monkeypatch, tmp_path):
    classes = {"com.acme.shop.CartTest": [("addsItems()", "SUCCESS")], "com.acme.shop.CartTests": [("extraWorks()", "SKIPPED")]}
    result, _ = asked(
        monkeypatch, tmp_path, ["com.acme.shop.CartTest.addsItems()"],
        asked_rows(classes, executor="SKIPPED", class_result={"com.acme.shop.CartTests": "SKIPPED"}),
    )
    assert result["passed"] is False and "SKIPPED without finishing it" in text(result), result


def test_a_whole_project_asked_for_needs_a_test_that_ran_in_it(monkeypatch, tmp_path):
    repo = multi(tmp_path)

    def script(argv, cwd, manifest):
        if "help" in argv:
            lines(manifest, init_line(), core_runs_web_does_not(cwd))
            return 0, ""
        lines(manifest, init_line(), core_runs_web_does_not(cwd), *listener(":core:test", {"com.acme.core.CoreTest": [("works()", "SUCCESS")]}), task_line(":core:test"))
        return 0, OK_CONSOLE

    fake = install(monkeypatch, script)
    assert checker.run_check(repo, tests=["core"], timeout=60, profile=JAVA)["passed"] is True
    assert fake.calls[-1][-1] == ":core:test"
    result = checker.run_check(repo, tests=["web"], timeout=60, profile=JAVA)
    assert result["passed"] is False  # the fake still ran only core's tests, so web has no test of its own


# ---- the record is the init script's; a build can add to it, and the first record wins -----------------------------


def test_the_first_graph_and_the_first_record_of_a_task_win(monkeypatch, tmp_path):
    def rows(repo):
        forged = [graph(repo, scheduled=False), task_line(":test", skip="UP-TO-DATE", executed=False)]
        return record(repo) + forged

    assert verdict(monkeypatch, tmp_path, rows)["passed"] is True


def test_the_init_script_does_nothing_in_a_build_with_a_parent_and_writes_no_dollar():
    script = gradle_check.INIT_SCRIPT
    assert "if (gradle.parent == null) {" in script and "$" not in script
    assert script.index("if (gradle.parent == null)") < script.index("addTestListener") < script.index("taskGraph.whenReady")
