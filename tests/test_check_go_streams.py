"""The verdict of `check` for Go, read from captured `go test -json` streams (no Go needed).

Each stream under tests/fixtures/go_check/captures was written by the real toolchain from the tiny
module beside it (capture.py has the exact commands). A verdict that says passed when tests failed,
did not run, or matched nothing is the failure these tests exist for.
"""

import json

import pytest

from go_check_support import MODULES, invocation, meta, names, run_of, stream_text, verdict
from ripple.go_check import Run, judge, parse_stream, parse_wants

PASSING = [
    "pass", "multimod_root", "twopkgs_a_only", "skipped_all", "subtests_slash", "subtests_space",
    "grid_x1", "grid_y2", "nested_services_api", "nested_services_web", "example_noout", "quiet_all",
    "nested_excluded_api", "nested_excluded_web", "bench_ok",
]  # fmt: skip
# Plain `go test` runs made under a GOFLAGS that left TestBad out: their streams ARE a clean pass, which is the point of
# them (a stream cannot show a test that never ran). ripple never makes such a run: its command line overrides the flag.
PLAIN_NARROWED = ["goflags_run_plain", "goflags_test_run_plain", "goflags_quoted_run_plain", "goflags_test_skip_plain"]


def test_every_capture_is_judged_and_the_passing_ones_are_the_real_passes():
    # The captures that are a pass: a run with nothing wrong. Everything else must come out as not passed.
    passed = {n for n in names() if verdict(n)["passed"]}
    assert passed == set(PASSING) | set(PLAIN_NARROWED)


def test_the_captures_without_ripples_overrides_are_exactly_the_plain_goflags_ones():
    plain = {n for n in names() if "-skip=" not in meta(n)["argv"]}
    assert plain == {"goflags_c_plain", "goflags_list_plain", *PLAIN_NARROWED}


@pytest.mark.parametrize("name", PASSING)
def test_a_clean_run_is_a_pass_with_nothing_to_say(name):
    result = verdict(name)
    assert result["passed"] is True
    assert result["problems"] == []
    assert result["failures"] == []
    assert result["exit_code"] == 0
    assert result["counts"]["passed"] >= 1


def test_pass_counts_and_summary():
    result = verdict("pass")
    assert result["counts"]["passed"] == 2
    assert result["summary"] == "2 passed, 0 failed, 0 skipped in 1 package"


def test_a_failing_test_is_named_as_file_and_test_with_its_first_message():
    result = verdict("fail")
    assert result["passed"] is False
    assert result["failures"] == ["FAILED calc_test.go::TestBad: calc_test.go:13: want 5, got 4"]
    assert result["failure_count"] == 1
    assert result["counts"] == {**result["counts"], "passed": 1, "failed": 1}


# ---- the outcome classes that must never read as a pass -----------------------------------------


def test_build_failure_is_not_a_pass_and_says_what_the_compiler_said():
    result = verdict("build_failure")
    assert result["passed"] is False
    assert any("build failed" in p and "undefined: missing" in p for p in result["problems"]), result["problems"]
    assert "1 failed to build" in result["summary"]
    # the package that did build ran its test, and that does not make the run a pass
    assert result["counts"]["passed"] == 1


@pytest.mark.parametrize("name, needle", [("test_build_failure", "undefined: Addd"), ("vet_failure", "wrong type string")])
def test_a_build_failure_in_the_test_file_or_vet_is_not_a_pass(name, needle):
    result = verdict(name)
    assert result["passed"] is False
    assert any("build failed" in p and needle in p for p in result["problems"]), result["problems"]
    assert result["counts"]["passed"] == 0


def test_a_package_with_no_test_files_alone_is_not_a_pass():
    result = verdict("no_test_files")
    assert result["passed"] is False
    assert result["problems"] == ["no tests ran (1 package with no test files)"]
    assert result["counts"]["packages_without_test_files"] == 1


def test_a_pattern_that_matches_nothing_is_not_a_pass_though_go_exits_zero():
    capture = meta("nomatch")
    assert capture["exit_code"] == 0  # go itself calls this a success
    result = verdict("nomatch")
    assert result["passed"] is False
    assert result["exit_code"] == 0
    assert result["problems"] == ["no tests ran (1 package that ran no test)"]


def test_a_requested_test_in_a_file_no_build_includes_is_not_a_pass():
    # TestTagged is declared in tagged_test.go, under //go:build integration: -run matches nothing
    result = verdict("tagged")
    assert meta("tagged")["exit_code"] == 0
    assert result["passed"] is False
    assert result["problems"] == ["tagged_test.go::TestTagged: no test of that name ran (the pattern matched nothing)"]


def test_a_test_binary_that_exits_zero_early_is_not_a_pass():
    capture = meta("exit_early")
    assert capture["exit_code"] == 0
    result = verdict("exit_early")
    assert result["passed"] is False
    assert result["problems"] == [
        "example.com/exit_early: the test binary exited without reporting a result (the last test started was TestExits)"
    ]
    # test2json closes the test it was in with a pass nothing gave: it is not counted as one
    assert result["counts"]["passed"] == 1
    assert result["counts"]["unreported"] == 1
    assert "ended with no result" in result["summary"]


def test_a_test_main_that_never_runs_the_tests_is_not_a_pass():
    assert meta("testmain_noop")["exit_code"] == 0
    result = verdict("testmain_noop")
    assert result["passed"] is False
    assert result["problems"] == [
        "example.com/testmain_noop: reported ok but never printed PASS, so its test binary may have exited "
        "before running its tests (a TestMain or init that calls os.Exit(0))"
    ]


def test_a_killed_test_binary_names_the_test_it_was_running():
    result = verdict("killed")
    assert result["passed"] is False
    assert result["failures"] == ["FAILED calc_test.go::TestKilled (did not finish): signal: killed"]


def test_a_panic_is_a_failure_with_the_panic_line():
    result = verdict("panic")
    assert result["passed"] is False
    assert len(result["failures"]) == 1
    assert result["failures"][0].startswith("FAILED calc_test.go::TestPanics: panic: runtime error: index out of range")
    assert result["counts"]["passed"] == 1  # TestAfter ran before it


def test_gos_own_timeout_is_a_failure_naming_the_test_that_hung():
    result = verdict("timeout")
    assert result["passed"] is False
    assert result["failures"] == ["FAILED calc_test.go::TestSlow (did not finish): panic: test timed out after 1s"]
    assert result["counts"]["passed"] == 1  # TestFast


def test_ripples_own_timeout_cuts_the_stream_and_the_verdict_says_so():
    # What a run killed for taking too long leaves: the stream up to the test that was running.
    lines = stream_text("timeout").splitlines(keepends=True)
    cut = next(i for i, line in enumerate(lines) if json.loads(line).get("Test") == "TestSlow" and json.loads(line)["Action"] == "run")
    result = verdict("timeout", text="".join(lines[: cut + 1]), timed_out=True)
    assert result["passed"] is False
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert any(p.startswith("timed out after 300s") for p in result["problems"])
    assert any("still running when it was killed: TestSlow" in p for p in result["problems"])
    assert result["failures"] == ["FAILED calc_test.go::TestSlow (did not finish)"]
    assert result["summary"].endswith("; timed out")


def test_a_run_that_started_no_package_and_printed_nothing_is_not_a_pass():
    result = verdict("pass", text="")
    assert result["passed"] is False
    assert any(p.startswith("go test produced no result") for p in result["problems"])


# ---- the events decide, not the exit code ------------------------------------------------------


def test_a_failing_event_beats_an_exit_code_of_zero():
    result = verdict("fail", runs=[run_of("fail", exit_code=0)])
    assert result["passed"] is False
    assert result["failure_count"] == 1


def test_a_nonzero_exit_with_clean_events_is_not_a_pass():
    result = verdict("pass", runs=[run_of("pass", exit_code=2)])
    assert result["passed"] is False
    assert any(p.startswith("go test exited 2 but its events show no failure") for p in result["problems"])


def test_a_line_that_is_not_an_event_is_reported():
    result = verdict("pass", text=stream_text("pass") + "this is not json\n")
    assert result["passed"] is False
    assert any("not an event" in p and "this is not json" in p for p in result["problems"])


def test_a_package_that_passes_with_a_test_that_never_finished_is_not_a_pass():
    lines = [json.loads(line) for line in stream_text("pass").splitlines()]
    for event in lines:
        if event.get("Test") == "TestAddZero" and event["Action"] == "pass":
            event["Action"] = "output"
            event["Output"] = "lost\n"
    result = verdict("pass", text="\n".join(json.dumps(e) for e in lines) + "\n")
    assert result["passed"] is False
    assert any("never finished: TestAddZero" in p for p in result["problems"])


# ---- subtests, skips, the same name in two packages ---------------------------------------------


def test_subtests_are_listed_under_their_parent_and_failed_ones_by_name():
    result = verdict("subtests_all")
    assert result["passed"] is False
    assert result["failures"] == [
        "FAILED calc_test.go::TestTable",
        "FAILED calc_test.go::TestTable/bad: calc_test.go:9: bad is bad",
        "FAILED calc_test.go::TestTable/bad2: calc_test.go:9: bad2 is bad",
    ]
    assert result["counts"]["passed"] == 1  # TestOther: subtests are counted apart
    assert result["counts"]["subtests"] == 5
    assert result["summary"] == "1 passed, 1 failed, 0 skipped (and 5 subtests) in 1 package"


def test_one_subtest_asked_for_is_judged_by_its_own_event():
    result = verdict("subtests_one")
    assert result["passed"] is False
    assert "FAILED calc_test.go::TestTable/bad: calc_test.go:9: bad is bad" in result["failures"]


def test_a_subtest_with_a_slash_and_one_with_a_space_pass_by_their_rewritten_names():
    assert verdict("subtests_slash")["passed"] is True
    assert verdict("subtests_space")["passed"] is True


def test_a_subtest_name_that_matched_nothing_is_not_a_pass_though_its_parent_passed():
    result = verdict("subtests_missing")
    assert result["passed"] is False
    assert result["counts"]["passed"] == 1  # TestTable itself ran and passed
    assert result["problems"] == ["calc_test.go::TestTable/nope: no test of that name ran (the pattern matched nothing)"]


def test_a_skipped_test_that_was_asked_for_is_not_a_pass_but_a_skip_among_others_is_fine():
    asked = verdict("skipped_only")
    assert asked["passed"] is False
    assert asked["problems"] == ["calc_test.go::TestSkipped: the test was skipped, so it did not run"]
    whole = verdict("skipped_all")
    assert whole["passed"] is True
    assert whole["counts"]["skipped"] == 1


def test_the_same_test_name_in_two_packages_is_told_apart():
    result = verdict("twopkgs_all")
    assert result["failures"] == ["FAILED b/b_test.go::TestSame: b_test.go:7: b is wrong"]
    assert result["counts"]["passed"] == 3 and result["counts"]["failed"] == 1
    # asked for in package a alone, the passing one is the verdict; b's failure is not part of it
    assert verdict("twopkgs_a_only")["passed"] is True
    assert verdict("twopkgs_b_only")["failures"] == ["FAILED b/b_test.go::TestSame: b_test.go:7: b is wrong"]


def test_a_test_that_ran_only_in_the_other_package_does_not_satisfy_the_request():
    # a/a_test.go::TestSame was asked for, and the stream only has b's TestSame
    result = verdict("twopkgs_b_only", runs=[run_of("twopkgs_b_only")])
    assert result["passed"] is False
    capture = meta("twopkgs_a_only")
    assert capture["request"] == ["a/a_test.go::TestSame"]

    repo = MODULES / "twopkgs"
    wants = parse_wants(repo, capture["request"])
    mixed = Run(invocation("twopkgs_a_only"), parse_stream(stream_text("twopkgs_b_only")), 0)
    answer = judge(repo, [mixed], wants, "x", 0.1, 300)
    assert answer["passed"] is False
    assert any("a/a_test.go::TestSame: no package ran there" in p for p in answer["problems"]), answer["problems"]


def test_a_multi_module_repo_runs_both_modules_and_a_failure_in_either_fails_it():

    runs = [run_of("multimod_root"), run_of("multimod_sub")]
    answer = judge(MODULES / "multimod", runs, [], "x", 0.1, 300)
    assert answer["passed"] is False
    assert answer["failures"] == ["FAILED sub/sub_test.go::TestSub: sub_test.go:7: sub is wrong"]
    assert answer["counts"]["passed"] == 1 and answer["counts"]["failed"] == 1
    assert any(n.startswith("2 `go test` runs") for n in answer["notes"])


def test_the_parser_keeps_what_the_stream_says_per_package_and_test():
    stream = parse_stream(stream_text("panic"))
    package = stream.packages["example.com/panic"]
    assert package.tests == {"TestAfter": "pass", "TestPanics": "fail"}
    assert package.result == "fail"
    assert package.detail["TestPanics"].startswith("panic: runtime error")
    built = parse_stream(stream_text("build_failure"))
    assert built.build_failed == ["example.com/build_failure [example.com/build_failure.test]"]
    assert built.packages["example.com/build_failure"].failed_build == built.build_failed[0]
