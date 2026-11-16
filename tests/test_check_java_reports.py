"""The verdict of `check` for Java, from captured `mvn test` runs (tests/fixtures/java_check): no JDK needed.

Each capture is a real run of a tiny Maven project (its output, the exit code, the surefire report files it
wrote, and the report files an earlier run left there). The outcome classes below are the ones the verdict must
never read as passed. Every guard that turns a would-be pass into a problem has a test that asserts its message,
so deleting the guard fails a test: the guards are named in the comment above each.
"""

from __future__ import annotations

import re

import pytest
from java_check_support import by_hand, make_repo, meta, names, run_of, stdout_of, verdict

from ripple import java_check as jc


def problems_text(result: dict) -> str:
    return " | ".join(result["problems"])


def assert_not_passed(result: dict, *fragments: str) -> None:
    assert result["passed"] is False, result
    for fragment in fragments:
        assert fragment in problems_text(result) + " | " + " | ".join(result["failures"]), (fragment, result)


# ---- what is a pass -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["pass", "method", "method_params", "method_nested", "methods_two", "class_file", "dir"])
def test_green_runs_pass_with_their_evidence(tmp_path, name):
    result = verdict(tmp_path, name)
    assert result["passed"] is True, result
    assert result["problems"] == [] and result["failures"] == []
    assert result["counts"]["passed"] > 0 and result["counts"]["failed"] == 0 and result["counts"]["errored"] == 0


def test_the_counts_come_from_the_reports(tmp_path):
    result = verdict(tmp_path, "pass")
    assert result["summary"] == "5 passed, 0 failed, 0 skipped in 3 test classes"
    # the nested class and the parameterized test are in it: 1 plain + 2 invocations + 1 nested + 1 of CartTests
    assert result["counts"]["classes"] == 3


# ---- failures, with their ids -------------------------------------------------------------------------


def test_a_failure_and_an_error_are_listed_as_file_class_method(tmp_path):
    result = verdict(tmp_path, "fail")
    assert_not_passed(result)
    assert result["failure_count"] == 2
    assert result["failures"][0].startswith("FAILED src/test/java/com/acme/shop/CartTest.java::CartTest.totalIsWrong: deliberately wrong")
    assert "CartTest.boom: kaboom" in result["failures"][1]
    assert result["counts"]["failed"] == 1 and result["counts"]["errored"] == 1


def test_a_method_that_fails_is_the_one_asked_for(tmp_path):
    result = verdict(tmp_path, "method_fail")
    assert_not_passed(result)
    assert result["failure_count"] == 1


def test_the_same_class_in_two_modules_is_told_apart_by_module(tmp_path):
    both = verdict(tmp_path / "all", "samename_all")
    assert both["counts"]["passed"] == 1 and both["counts"]["failed"] == 1
    assert both["failures"][0].startswith("FAILED b/src/test/java/com/acme/SameTest.java::SameTest.same")
    one = verdict(tmp_path / "b", "samename_b")
    assert_not_passed(one)
    assert one["failure_count"] == 1


# guard: a failing test with an exit status of 0 (testFailureIgnore) is a failure, said so
def test_exit_zero_with_a_failing_test_is_not_passed(tmp_path):
    result = verdict(tmp_path, "failure_ignored")
    assert_not_passed(result)
    assert result["failure_count"] == 1
    assert any("exited 0 although tests failed" in n for n in result["notes"])
    assert any("testFailureIgnore" in n for n in result["notes"])


# ---- Maven's silent exit 0 -------------------------------------------------------------------------------


# guard: a wanted method with no testcase in a fresh report (Maven exits 0 and runs 0 tests)
def test_a_selector_that_matches_nothing_in_an_existing_class_is_not_passed(tmp_path):
    assert "BUILD SUCCESS" in stdout_of("method_nomatch") and meta("method_nomatch")["exit_code"] == 0
    result = verdict(tmp_path, "method_nomatch", invocation=by_hand("com.acme.shop.CartTest", "nosuch"))
    assert_not_passed(result, "CartTest.nosuch: no test ran", "the selector matched nothing")


def test_a_class_selector_that_matches_nothing_is_not_passed(tmp_path):
    result = verdict(tmp_path, "class_nomatch", invocation=by_hand("com.acme.shop.CartTes", selectors=("com.acme.shop.CartTes",)))
    assert_not_passed(result, "No tests matching pattern")


# guard: no testcase at all in this run (the build says nothing ran)
def test_a_build_with_no_tests_is_not_passed(tmp_path):
    result = verdict(tmp_path, "no_tests")
    assert_not_passed(result, "no tests ran")
    assert result["counts"]["passed"] == 0


# guard: the build was told to skip its tests (skipTests / maven.test.skip): the log says so
def test_skip_tests_in_the_pom_is_not_passed_and_is_named(tmp_path):
    result = verdict(tmp_path, "skip_tests")
    assert_not_passed(result, "the build skips its tests", "no results for 1 test class")
    assert any("skipTests" in n for n in result["notes"])


# ---- stale reports -------------------------------------------------------------------------------------


# guard: fresh() (a report is this run's only if it changed since the snapshot and is not older than the start)
def test_a_stale_passing_report_is_not_this_runs_result_when_the_selector_matched_nothing(tmp_path):
    """The earlier run left TEST-com.acme.shop.CartTest.xml saying everything passed; this run exits 0 and ran nothing."""
    repo, project, run = run_of(tmp_path, "stale_method_nomatch", invocation=by_hand("com.acme.shop.CartTest", "nosuch"))
    assert run.stale == 3 and run.cases == []
    assert list((repo / "target" / "surefire-reports").glob("TEST-*.xml")), "the stale reports are on disk"
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert_not_passed(result, "CartTest.nosuch: no test ran")
    assert result["counts"]["passed"] == 0
    assert any("3 report files from earlier runs ignored" in n for n in result["notes"])


def test_a_stale_passing_report_is_not_this_runs_result_when_the_tests_do_not_compile(tmp_path):
    """The earlier run's reports say PASSED for every class; this run fails to compile the tests."""
    result = verdict(tmp_path, "stale_test_compile_error")
    assert_not_passed(result, "compilation failed: CartTest.java:22 cannot find symbol")
    assert result["counts"]["passed"] == 0
    assert "no results for 3 test classes" in problems_text(result)
    assert any("earlier runs ignored" in n for n in result["notes"])


def test_a_report_rewritten_by_this_run_is_fresh_and_an_untouched_one_is_not(tmp_path):
    repo = make_repo(tmp_path, "pass")
    from java_check_support import CAPTURES, HOUR_NS, place
    import time

    place(repo, CAPTURES / "pass" / "reports", time.time_ns() - HOUR_NS)
    before = jc.snapshot(repo)
    started = time.time_ns()
    mine, stale = jc.fresh(repo, before, started)
    assert mine == [] and stale == 3
    target = repo / "target" / "surefire-reports" / "TEST-com.acme.shop.CartTests.xml"
    target.write_bytes(target.read_bytes())  # the same bytes, written now
    mine, stale = jc.fresh(repo, before, started)
    assert mine == ["target/surefire-reports/TEST-com.acme.shop.CartTests.xml"] and stale == 2


# guard: the clock floor of fresh(): a file whose signature changed but that is older than the start is not this run's
def test_a_report_older_than_the_run_is_not_fresh_even_if_it_differs_from_the_snapshot(tmp_path):
    import os
    import time

    repo = make_repo(tmp_path, "pass")
    from java_check_support import CAPTURES, HOUR_NS, place

    place(repo, CAPTURES / "pass" / "reports", time.time_ns() - HOUR_NS)
    before = jc.snapshot(repo)
    started = time.time_ns()
    target = repo / "target" / "surefire-reports" / "TEST-com.acme.shop.CartTests.xml"
    target.write_text(target.read_text() + "\n")  # changed size, but stamped an hour before the run began
    old = time.time_ns() - HOUR_NS
    os.utime(target, ns=(old, old))
    mine, _ = jc.fresh(repo, before, started)
    assert mine == []


# ---- builds that did not build ----------------------------------------------------------------------------


# guards: compile errors are named, with file and line (no repo path in them)
@pytest.mark.parametrize("name,file", [("main_compile_error", "Cart.java:11"), ("test_compile_error", "CartTest.java:22")])
def test_a_compile_failure_is_not_passed_and_names_the_line(tmp_path, name, file):
    result = verdict(tmp_path, name)
    assert_not_passed(result, f"compilation failed: {file} cannot find symbol", "no results for 1 test class")
    assert "/tmp" not in problems_text(result)
    assert len([p for p in result["problems"] if p.startswith("compilation failed")]) == 1  # Maven prints each error twice


# guard: the generic one, Maven failed and no report shows a failure
def test_maven_failing_with_no_failing_test_is_not_passed(tmp_path):
    output = stdout_of("test_compile_error").replace("[ERROR] COMPILATION ERROR", "[ERROR] SOMETHING").replace(".java:[", ".txt:[")
    output = re.sub(r"\[ERROR\] Failed to execute goal.*", "[ERROR] Failed to run it", output)
    result = verdict(tmp_path, "test_compile_error", output=output)
    assert_not_passed(result, "maven exited 1 but its reports show no failure")


# guard: surefire failing with no message and no failing testcase (a crash)
@pytest.mark.parametrize("name", ["exit_zero", "halt"])
def test_a_jvm_crash_or_system_exit_is_not_passed(tmp_path, name):
    result = verdict(tmp_path, name)
    assert_not_passed(result, "a test JVM crashed or called System.exit", "the surefire goal failed with no failing test")
    assert "no results for 2 test classes" in problems_text(result)  # the crashed class took its neighbours with it
    assert result["counts"]["passed"] == 0


def test_a_system_exit_zero_is_caught_even_when_maven_exits_zero(tmp_path):
    """If a build tool swallowed the crash (exit 0 and BUILD SUCCESS), the missing reports still fail the verdict."""
    output = stdout_of("exit_zero").replace("BUILD FAILURE", "BUILD SUCCESS")
    result = verdict(tmp_path, "exit_zero", output=output, exit_code=0)
    assert_not_passed(result, "no results for 2 test classes")


# guard: a fork timeout leaves a report that says the test passed and exits 1
def test_a_surefire_fork_timeout_is_not_passed_although_the_report_says_passed(tmp_path):
    result = verdict(tmp_path, "fork_timeout")
    assert result["counts"]["passed"] == 2 and result["counts"]["failed"] == 0
    assert_not_passed(result, "There was a timeout in the fork")
    assert result["exit_code"] == 1


# guard: ripple's own time limit
def test_a_run_killed_for_time_is_not_passed_and_says_partial(tmp_path):
    result = verdict(tmp_path, "ripple_timeout")
    assert_not_passed(result, "timed out after 40s")
    assert result["timed_out"] is True and result["exit_code"] is None
    assert result["summary"].endswith("timed out")


def test_a_test_that_times_out_under_junit_is_a_failure(tmp_path):
    result = verdict(tmp_path, "junit_timeout")
    assert_not_passed(result)
    assert "CartTest.tooSlow: tooSlow() timed out after 1 second" in result["failures"][0]
    assert result["problems"] == []  # the failing report explains Maven's exit status: no second story


# guard: exit status 0 without BUILD SUCCESS
def test_exit_zero_without_build_success_is_not_passed(tmp_path):
    output = stdout_of("pass").replace("[INFO] BUILD SUCCESS", "[INFO] ")
    result = verdict(tmp_path, "pass", output=output)
    assert_not_passed(result, "maven exited 0 without reporting BUILD SUCCESS")


# ---- modules --------------------------------------------------------------------------------------------


# guards: reactor SKIPPED, and no results from a module that was expected to run tests
def test_a_module_that_never_reached_its_tests_is_not_passed(tmp_path):
    result = verdict(tmp_path, "multimod_fail")
    assert_not_passed(result, "modules skipped", "no results in module web for 1 test class: com.acme.web.WebTest")
    assert result["counts"]["passed"] == 1 and result["counts"]["failed"] == 1  # util ran, core failed
    assert result["failures"][0].startswith("FAILED core/src/test/java/com/acme/core/CoreTest.java::CoreTest.coreIsWrong")


# guard: _check_modules alone (the reactor summary line is gone, as with a Maven that words it differently)
def test_a_module_with_tests_and_no_results_is_not_passed_without_the_reactor_summary(tmp_path):
    output = stdout_of("multimod_fail")
    output = output.replace("SKIPPED", "SUCCESS [  0.1 s]").replace("BUILD FAILURE", "BUILD SUCCESS")
    result = verdict(tmp_path, "multimod_fail", output=output, exit_code=0)
    assert_not_passed(result, "no results in module web for 1 test class")
    assert "modules skipped" not in problems_text(result)


def test_a_module_with_no_test_classes_is_a_note_not_a_problem(tmp_path):
    result = verdict(tmp_path / "ok", "multimod_class", invocation=jc.Invocation(None, (), False, ()))
    # the aggregator root and `core` have no result to show and `web` (the only one asked for) does
    assert result["passed"] is False  # core's tests were expected and did not run: the whole build was asked for
    assert "no results in module core" in problems_text(result)


def test_the_module_run_of_one_module_with_dependencies_says_it_built_upstream(tmp_path):
    result = verdict(tmp_path, "multimod_class")
    assert result["passed"] is True
    assert any(n.startswith("-am:") for n in result["notes"])


# ---- tests surefire's patterns do not pick up -----------------------------------------------------------


# guard: _check_modules on the whole build
def test_a_test_class_surefire_does_not_pick_up_is_not_passed(tmp_path):
    assert meta("not_picked_up")["exit_code"] == 0 and "BUILD SUCCESS" in stdout_of("not_picked_up")
    result = verdict(tmp_path, "not_picked_up")
    assert_not_passed(result, "no results for 1 test class: com.acme.shop.CartChecks", "surefire picks up Test*")
    assert result["counts"]["passed"] == 1


# ---- skipped, disabled -----------------------------------------------------------------------------------


def test_a_disabled_test_that_was_asked_for_is_not_passed(tmp_path):
    result = verdict(tmp_path, "disabled_method")
    assert_not_passed(result, "skipped or disabled, so it did not run")
    assert result["counts"]["skipped"] == 1 and result["counts"]["passed"] == 0


def test_disabled_tests_in_a_whole_run_are_counted_and_named_not_hidden(tmp_path):
    result = verdict(tmp_path, "disabled_all")
    assert result["passed"] is True  # nothing asked for by name was skipped, and the rest passed
    assert result["counts"]["skipped"] == 2
    assert any("2 tests skipped or disabled" in n for n in result["notes"])
    assert "2 skipped" in result["summary"]


# guard: every test that ran was skipped
def test_a_run_where_every_test_was_skipped_is_not_passed(tmp_path):
    repo, project, run = run_of(tmp_path, "disabled_method", invocation=jc.Invocation(None, (), False, ()))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is False
    assert "every test that ran was skipped" in problems_text(result)


# ---- overloads, parameterized, nested ---------------------------------------------------------------------


def test_a_method_name_that_two_tests_share_runs_both_and_says_so(tmp_path):
    result = verdict(tmp_path, "overloads_method")
    assert_not_passed(result)
    note = next(n for n in result["notes"] if "overloaded" in n)
    assert "2 test methods of that name" in note and "(), (TestInfo)" in note
    assert "CartTest.check(TestInfo)" in result["failures"][0]  # the failing overload is named, not folded into the one asked for


def test_the_overload_asked_for_by_its_types_must_be_the_one_that_ran(tmp_path):
    repo, project, run = run_of(tmp_path, "overloads_method")
    wants = jc.parse_wants(repo, project, jc.normalize(repo, ["com.acme.shop.CartTest.check(int)"]) if False else ["src/test/java/com/acme/shop/CartTest.java::CartTest.check(String)"])
    run.invocation = jc.Invocation(None, ("com.acme.shop.CartTest#check",), False, tuple(wants))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert "only other overloads ran" in problems_text(result)


def test_a_parameterized_test_says_how_many_invocations_ran(tmp_path):
    result = verdict(tmp_path, "method_params")
    assert result["counts"]["passed"] == 2
    assert any("2 invocations of a parameterized test" in n for n in result["notes"])


def test_a_nested_class_method_is_matched_by_its_binary_name(tmp_path):
    result = verdict(tmp_path, "method_nested")
    assert result["passed"] is True and result["counts"]["passed"] == 1


def test_a_class_asked_for_runs_its_nested_class_and_a_prefix_class_is_not_it(tmp_path):
    result = verdict(tmp_path, "class_file")  # CartTest, with CartTests beside it: asked for CartTest only
    assert result["passed"] is True
    assert result["counts"]["passed"] == 4  # CartTest (1 + 2 invocations) and its nested Receipts (1); CartTests is not in it
    assert not any("not asked for" in n for n in result.get("notes", []))


# guard: a test that ran and was not asked for is said so
def test_a_test_that_ran_and_was_not_asked_for_is_named(tmp_path):
    repo, project, run = run_of(tmp_path, "pass")
    want = jc.Want("x", "", "com.acme.shop.CartTest", "addsItems")
    run.invocation = jc.Invocation(None, ("com.acme.shop.CartTest#addsItems",), False, (want,))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    note = next(n for n in result["notes"] if "not asked for" in n)
    assert "4 tests ran that were not asked for" in note and "CartTests.extraWorks" in note


# ---- the reports themselves --------------------------------------------------------------------------------


# guard: a report that does not parse
def test_a_report_that_is_cut_off_is_not_usable_and_not_passed(tmp_path):
    repo, project, run = run_of(tmp_path, "pass")
    path = next((repo / "target" / "surefire-reports").glob("TEST-com.acme.shop.CartTests.xml"))
    path.write_text(path.read_text()[:150])
    _, reason = jc.read_report(repo, project, path.relative_to(repo).as_posix())
    assert "not readable" in reason
    run.unreadable = [reason]
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert_not_passed(result, "report not usable")


# guard: a report whose own counts say more failed than it lists
def test_a_report_whose_counts_disagree_with_its_cases_is_not_usable(tmp_path):
    repo, project, run = run_of(tmp_path, "pass")
    path = repo / "target" / "surefire-reports" / "TEST-com.acme.shop.CartTests.xml"
    path.write_text(path.read_text().replace('failures="0"', 'failures="2"', 1))
    cases, reason = jc.read_report(repo, project, path.relative_to(repo).as_posix())
    assert reason and "says 2 failed or errored but lists 0" in reason


def test_a_flaky_test_is_a_pass_that_is_counted(tmp_path):
    repo, project, run = run_of(tmp_path, "pass")
    path = repo / "target" / "surefire-reports" / "TEST-com.acme.shop.CartTests.xml"
    text = path.read_text()
    path.write_text(re.sub(r'(<testcase name="extraWorks"[^>]*?)/>', r'\1><flakyFailure message="m" type="t">x</flakyFailure></testcase>', text))
    cases, reason = jc.read_report(repo, project, path.relative_to(repo).as_posix())
    assert reason is None and cases[0].status == "pass" and cases[0].flaky


# ---- settings that narrow or skip tests ------------------------------------------------------------------


@pytest.mark.parametrize(
    "environ,fragment",
    [
        ({"MAVEN_OPTS": "-Xmx1g -DskipTests"}, "$MAVEN_OPTS sets -DskipTests"),
        ({"MAVEN_ARGS": "-Dmaven.test.skip=true"}, "$MAVEN_ARGS sets -Dmaven.test.skip"),
        ({"MAVEN_OPTS": "-Dtest=Nothing"}, "$MAVEN_OPTS sets -Dtest"),
        ({"MAVEN_OPTS": "-Dsurefire.excludesFile=x"}, "$MAVEN_OPTS sets -Dsurefire.excludesFile"),
        ({"MAVEN_OPTS": "-Dgroups=fast"}, "$MAVEN_OPTS sets -Dgroups"),
        ({"MAVEN_OPTS": "-Dmaven.test.failure.ignore=true"}, "sets -Dmaven.test.failure.ignore"),
    ],
)
def test_environment_that_narrows_or_skips_tests_is_a_named_problem(tmp_path, environ, fragment):
    repo = make_repo(tmp_path, "pass")
    found = jc.hazards(repo, environ)
    assert any(fragment in line for line in found), found
    _, project, run = run_of(tmp_path / "x", "pass")
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300, settings=found)
    assert_not_passed(result, fragment, "can narrow or skip tests")


def test_harmless_settings_are_not_hazards(tmp_path):
    repo = make_repo(tmp_path, "pass")
    (repo / ".mvn").mkdir()
    (repo / ".mvn" / "maven.config").write_text("-Dmaven.repo.local=/tmp/x\n-o\n--batch-mode\n")
    assert jc.hazards(repo, {"MAVEN_OPTS": "-Xmx2g -Dhttps.proxyHost=p -Dmaven.compiler.release=21", "MAVEN_ARGS": "-T 1C"}) == []


def test_maven_config_and_jvm_config_of_the_repo_are_read(tmp_path):
    repo = make_repo(tmp_path, "pass")
    (repo / ".mvn").mkdir()
    (repo / ".mvn" / "maven.config").write_text("-DskipTests\n")
    (repo / ".mvn" / "jvm.config").write_text("-Dtest=Foo -Xmx1g\n")
    found = jc.hazards(repo, {})
    assert ".mvn/maven.config sets -DskipTests" in found and ".mvn/jvm.config sets -Dtest" in found


def test_pom_settings_that_may_narrow_are_named_in_the_verdict(tmp_path):
    result = verdict(tmp_path, "skip_tests")
    assert any("pom.xml configures <skipTests>" in n for n in result["notes"])


def test_every_capture_has_a_test_somewhere_in_the_java_check_tests():
    """A capture nobody asserts on is a fixture that proves nothing: the list is kept honest by name."""
    import glob
    import os

    here = os.path.dirname(__file__)
    sources = "".join(open(p).read() for p in glob.glob(os.path.join(here, "test_check_java_*.py")))
    missing = [n for n in names() if f'"{n}"' not in sources]
    assert missing == []


# guard: a goal that failed for a reason of its own, beside failing tests that explain Maven's exit status
def test_a_build_failure_beside_failing_tests_is_named_too(tmp_path):
    output = stdout_of("fail") + (
        "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.13.0:compile (default-compile) "
        "on project web: Could not resolve dependencies for project g:web:jar:1: Cannot access central in offline mode\n"
    )
    result = verdict(tmp_path, "fail", output=output)
    assert_not_passed(result, "build failed: org.apache.maven.plugins:maven-compiler-plugin", "Could not resolve dependencies")
    assert result["failure_count"] == 2


# guard: a requested test that was skipped, named without its parameter types (the overload branch is not what says it)
def test_a_disabled_test_asked_for_without_types_is_not_passed(tmp_path):
    inv = by_hand("com.acme.shop.CartTest", "skipped")
    result = verdict(tmp_path, "disabled_method", invocation=inv)
    assert_not_passed(result, "CartTest.skipped: skipped or disabled, so it did not run")


# guard: the overload asked for was skipped while another overload of the name ran
def test_the_overload_asked_for_is_skipped_while_another_ran(tmp_path):
    repo, project, run = run_of(tmp_path, "overloads_method")
    for case in run.cases:
        if case.signature is None:
            case.status = "skip"
    want = jc.Want("x", "", "com.acme.shop.CartTest", "check", "", 2)
    run.invocation = jc.Invocation(None, ("com.acme.shop.CartTest#check",), False, (want,))
    result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert_not_passed(result, "the overload asked for was skipped or disabled")
