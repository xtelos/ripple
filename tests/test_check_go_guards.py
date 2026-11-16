"""Verdict guards found in review, judged from captured `go test -json` streams (no Go needed).

Each stream was written by the real toolchain (tests/fixtures/go_check/capture.py): a test binary that
exits 0 before it runs a test, a directory with no test to run, a grid of subtests where the siblings of
the ones asked for fail, an Example with no output comment, and GOFLAGS narrowing a run.
"""

import pytest

from go_check_support import BASE, MODULES, meta, run_of, stream_text, verdict
from ripple.go_check import Want, judge, parse_stream, parse_wants

SILENT = "reported ok but never printed PASS, so its test binary may have exited before running its tests"


def silent(path):
    return f"example.com/silent_exit/{path}: {SILENT} (a TestMain or init that calls os.Exit(0))"


# ---- MED-1: ok without evidence that the tests ran or were legitimately absent -----------------


def test_the_stream_tells_a_silent_exit_from_a_package_with_nothing_to_run():
    # a package that has nothing to run prints PASS (and, for a pattern that matched nothing, the warning);
    # a binary that exited before m.Run prints neither, only the ok line
    quiet = parse_stream(stream_text("silent_exit_all")).packages
    assert quiet["example.com/silent_exit/noop"].printed_pass is False
    assert quiet["example.com/silent_exit/initexit"].printed_pass is False
    assert quiet["example.com/silent_exit/ok"].printed_pass is True
    assert parse_stream(stream_text("nomatch")).packages["example.com/nomatch"].printed_pass is True
    assert parse_stream(stream_text("no_test_files")).packages["example.com/no_test_files"].printed_pass is False


def test_a_silent_package_among_packages_that_ran_is_not_a_pass_with_no_arguments():
    capture = meta("silent_exit_all")
    assert capture["exit_code"] == 0  # go calls it ok
    result = verdict("silent_exit_all")
    assert result["passed"] is False
    assert sorted(result["problems"]) == [silent("initexit"), silent("noop")]
    assert result["counts"]["passed"] == 1  # the ok package's test did run


def test_a_silent_package_among_packages_that_ran_is_not_a_pass_for_a_directory_argument():
    result = verdict("silent_exit_dir")
    assert result["passed"] is False
    assert sorted(result["problems"]) == [silent("initexit"), silent("noop")]


def test_a_silent_package_alone_is_not_a_pass_through_a_directory_argument():
    result = verdict("silent_exit_one")
    assert result["passed"] is False
    assert result["problems"] == [silent("initexit"), "initexit: no test ran there"]


def test_a_package_that_prints_pass_with_no_test_is_still_only_a_note_beside_tests_that_ran():
    # legitimately nothing to run (benchmarks, helpers, a pattern matching nothing in one package): PASS was printed
    result = verdict("nomatch")
    assert result["passed"] is False and result["problems"] == ["no tests ran (1 package that ran no test)"]


# ---- MED-3: the run is exactly what was asked --------------------------------------------------


def test_the_cross_product_pattern_ran_siblings_nobody_asked_for():
    # what `^TestGrid$/^(x|y)$/^(1|2)$` did: x/2 and y/1 ran, and fail
    stream = parse_stream(stream_text("grid_cross_product"))
    tests = stream.packages["example.com/grid"].tests
    assert tests["TestGrid/x/2"] == "fail" and tests["TestGrid/y/1"] == "fail"


def test_each_requested_grid_cell_runs_alone_and_passes():
    for name, ran, not_ran in [("grid_x1", "TestGrid/x/1", "TestGrid/x/2"), ("grid_y2", "TestGrid/y/2", "TestGrid/y/1")]:
        tests = parse_stream(stream_text(name)).packages["example.com/grid"].tests
        assert tests[ran] == "pass" and not_ran not in tests
        assert verdict(name)["passed"] is True


def test_the_two_runs_for_two_grid_cells_pass_together_and_none_of_the_siblings_fails_it():
    repo = MODULES / "grid"
    wants = parse_wants(repo, ["calc_test.go::TestGrid/x/1", "calc_test.go::TestGrid/y/2"])
    answer = judge(repo, [run_of("grid_x1"), run_of("grid_y2")], wants, "x", 0.1, 300)
    assert answer["passed"] is True and answer["failures"] == []


# ---- LOW-1: an Example with no output comment is never run -------------------------------------


def test_go_compiles_but_never_runs_an_example_with_no_output_comment():
    stream = parse_stream(stream_text("example_noout_unrun"))
    assert stream.packages["example.com/example_noout"].tests == {}
    repo = MODULES / "example_noout"
    asked = parse_wants(repo, ["calc_test.go::TestAdd"])
    assert asked[0].name == "TestAdd"
    unrun = judge(repo, [run_of("example_noout_unrun")], [Want("", ("ExampleAdd",), "calc_test.go")], "x", 0.1, 300)
    # the file was asked for as a whole: the test that did not run is named, not just the file
    assert unrun["problems"] == ["calc_test.go::ExampleAdd: no test of that name ran (the pattern matched nothing)"]


def test_a_whole_file_leaves_out_the_example_that_go_would_never_run():
    wants = parse_wants(MODULES / "example_noout", ["calc_test.go"])
    assert sorted(w.name for w in wants) == ["ExampleAdd_unordered", "ExampleAdd_withOutput", "TestAdd"]
    assert verdict("example_noout")["passed"] is True


def test_an_example_with_no_output_comment_asked_for_by_name_says_why_it_is_refused():
    with pytest.raises(ValueError, match="no // Output:"):
        parse_wants(MODULES / "example_noout", ["calc_test.go::ExampleAdd"])


# ---- LOW-3: a directory argument with nothing to run is not a pass -----------------------------


def test_a_directory_of_packages_with_no_test_files_is_not_a_pass():
    result = verdict("no_test_files_dir")
    assert result["passed"] is False
    assert result["problems"] == [".: no test ran there"]


def test_a_directory_whose_tests_all_skip_is_not_a_pass():
    result = verdict("allskip_dir")
    assert result["passed"] is False
    assert result["counts"]["skipped"] == 2 and result["counts"]["passed"] == 0
    assert result["problems"] == [".: no test ran there"]


# ---- nested modules: a directory of them ------------------------------------------------------


def test_the_root_module_finds_no_package_below_a_directory_that_holds_only_nested_modules():
    capture = meta("nested_services_holder")
    assert capture["exit_code"] == 1
    assert "matched no packages" in capture["stderr"]
    assert capture["argv"][-1] == "./services/..."


def test_a_directory_of_nested_modules_with_a_file_go_leaves_out_of_the_holder_is_a_pass_when_the_nested_runs_are():
    holder, api, web = run_of("nested_excluded_holder"), run_of("nested_excluded_api"), run_of("nested_excluded_web")
    capture = meta("nested_excluded_holder")
    assert capture["exit_code"] == 1 and stream_text("nested_excluded_holder") == ""
    assert "matched no packages" in capture["stderr"]
    repo = MODULES / "nested_excluded"
    answer = judge(repo, [holder, api, web], parse_wants(repo, ["services"]), "x", 0.1, 300)
    assert answer["passed"] is True and answer["problems"] == [], answer
    assert answer["counts"]["passed"] == 2 and answer["exit_code"] == 0
    # the order the planner makes them in is not what makes it a pass
    assert judge(repo, [api, web, holder], parse_wants(repo, ["services"]), "x", 0.1, 300)["passed"] is True


def test_the_empty_holder_run_is_only_empty_when_a_nested_module_below_it_ran_packages():
    repo = MODULES / "nested_excluded"
    wants = parse_wants(repo, ["services"])
    holder = run_of("nested_excluded_holder")
    alone = judge(repo, [holder], wants, "x", 0.1, 300)
    assert alone["passed"] is False
    assert any("matched no packages" in p for p in alone["problems"]), alone["problems"]
    # a run below the directory that ran nothing does not make it empty
    nothing_ran = run_of("nested_excluded_api", text="", exit_code=1)
    assert judge(repo, [holder, nothing_ran], wants, "x", 0.1, 300)["passed"] is False
    # and neither does a run in another directory
    elsewhere = judge(repo, [holder, run_of("pass")], wants, "x", 0.1, 300)
    assert elsewhere["passed"] is False
    assert any("matched no packages" in p for p in elsewhere["problems"]), elsewhere["problems"]


def test_a_holder_run_that_matched_no_packages_with_nothing_below_it_is_still_a_problem_as_the_captured_run_shows():
    result = verdict("nested_services_holder")
    assert result["passed"] is False
    assert any("matched no packages" in p for p in result["problems"])


def test_only_matched_no_packages_alone_on_stderr_makes_a_holder_run_empty():
    repo = MODULES / "nested_excluded"
    wants = parse_wants(repo, ["services"])
    api, web = run_of("nested_excluded_api"), run_of("nested_excluded_web")

    def holder_with(stderr, exit_code=1, **changes):
        holder = run_of("nested_excluded_holder")
        holder.stderr = stderr
        holder.exit_code = exit_code
        for key, value in changes.items():
            setattr(holder, key, value)
        return holder

    clean = 'go: warning: "./services/..." matched no packages\nno packages to test\n'
    assert judge(repo, [holder_with(clean), api, web], wants, "x", 0.1, 300)["passed"] is True
    for stderr in [clean + "go: some other trouble\n", "", "go: cannot find main module\n"]:
        assert judge(repo, [holder_with(stderr), api, web], wants, "x", 0.1, 300)["passed"] is False, stderr
    assert judge(repo, [holder_with(clean, timed_out=True, exit_code=None), api, web], wants, "x", 0.1, 300)["passed"] is False


# ---- a package with no test event needs go's own word that it had nothing to run --------------


def test_the_stream_shows_go_s_notice_that_there_was_nothing_to_run_and_a_printed_pass_does_not():
    quiet = parse_stream(stream_text("quiet_all")).packages
    for name in ("benchonly", "helpers", "exnoout"):
        assert quiet[f"example.com/quiet/{name}"].nothing_to_run is True, name
        assert quiet[f"example.com/quiet/{name}"].tests == {}
    assert parse_stream(stream_text("nomatch")).packages["example.com/nomatch"].nothing_to_run is True
    printed = parse_stream(stream_text("printpass_all")).packages
    assert printed["example.com/printpass/printpass"].printed_pass is True  # the line says PASS, as go's own would
    assert printed["example.com/printpass/printpass"].nothing_to_run is False
    assert printed["example.com/printpass/printpass"].tests == {}


def test_a_testmain_that_prints_pass_and_exits_zero_is_not_a_pass_beside_a_package_that_ran():
    assert meta("printpass_all")["exit_code"] == 0  # go calls it ok
    result = verdict("printpass_all")
    assert result["passed"] is False
    assert [p for p in result["problems"] if "printpass/printpass" in p] == [
        'example.com/printpass/printpass: reported ok with no test event and without go\'s "no tests to run" notice, so its '
        "test binary may have exited before running its tests (a TestMain or init that prints PASS and calls os.Exit(0))"
    ]
    assert result["counts"]["passed"] == 1  # ok's test ran


def test_a_printed_pass_is_not_a_pass_for_a_directory_argument_or_alone_either():
    repo = MODULES / "printpass"
    everything = run_of("printpass_all")
    for tests in (["."], ["printpass"]):
        answer = judge(repo, [everything], parse_wants(repo, tests), "x", 0.1, 300)
        assert answer["passed"] is False, tests
        assert any("printpass/printpass" in p and "no tests to run" in p for p in answer["problems"]), answer["problems"]
    only = run_of("printpass_all", text="".join(l for l in stream_text("printpass_all").splitlines(keepends=True) if "printpass/ok" not in l))
    answer = judge(repo, [only], [], "x", 0.1, 300)
    assert answer["passed"] is False and any("no tests to run" in p for p in answer["problems"])


def test_every_package_that_legitimately_has_no_test_to_run_is_a_pass_and_a_note_not_a_failure():
    # quiet: benchmarks only, helpers only, an example with no output comment only, a fuzz target only (its seed
    # corpus runs as a subtest), a package whose test file is build-excluded, one whose every file is
    assert meta("quiet_all")["exit_code"] == 0
    result = verdict("quiet_all")
    assert result["passed"] is True and result["problems"] == [] and result["failures"] == []
    assert result["counts"]["passed"] == 2  # ok's TestOK and fuzzonly's FuzzX
    assert result["counts"]["packages_without_test_files"] == 1  # excluded: go says "no test files"
    assert any(n.startswith("3 packages passed without running a test") for n in result["notes"]), result["notes"]


# ---- the split runs of one test are counted once ----------------------------------------------


def test_two_failing_cells_of_one_grid_are_one_test_in_one_package_and_one_failure_line():
    repo = MODULES / "grid"
    wants = parse_wants(repo, ["calc_test.go::TestGrid/x/2", "calc_test.go::TestGrid/y/1"])
    answer = judge(repo, [run_of("grid_x2"), run_of("grid_y1")], wants, "x", 0.1, 300)
    assert answer["passed"] is False
    assert answer["failures"] == [
        "FAILED calc_test.go::TestGrid",
        "FAILED calc_test.go::TestGrid/x",
        "FAILED calc_test.go::TestGrid/x/2: calc_test.go:11: x/2 was not asked for",
        "FAILED calc_test.go::TestGrid/y",
        "FAILED calc_test.go::TestGrid/y/1: calc_test.go:11: y/1 was not asked for",
    ]
    assert answer["failure_count"] == 5  # m11 had 6: TestGrid twice
    assert answer["counts"]["failed"] == 1 and answer["counts"]["subtests"] == 4 and answer["counts"]["packages"] == 1
    assert answer["summary"] == "0 passed, 1 failed, 0 skipped (and 4 subtests) in 1 package"


def test_two_passing_cells_are_one_test_one_package_and_not_a_count_per_run():
    repo = MODULES / "grid"
    wants = parse_wants(repo, ["calc_test.go::TestGrid/x/1", "calc_test.go::TestGrid/y/2"])
    answer = judge(repo, [run_of("grid_x1"), run_of("grid_y2")], wants, "x", 0.1, 300)
    assert answer["passed"] is True
    assert answer["counts"]["passed"] == 1 and answer["counts"]["subtests"] == 4 and answer["counts"]["packages"] == 1


@pytest.mark.parametrize("order", [("grid_x1", "grid_y1"), ("grid_y1", "grid_x1")])
def test_a_parent_that_passes_in_one_split_run_and_fails_in_another_is_a_failure_whichever_came_first(order):
    repo = MODULES / "grid"
    assert parse_stream(stream_text("grid_x1")).packages["example.com/grid"].tests["TestGrid"] == "pass"
    assert parse_stream(stream_text("grid_y1")).packages["example.com/grid"].tests["TestGrid"] == "fail"
    wants = parse_wants(repo, ["calc_test.go::TestGrid/x/1", "calc_test.go::TestGrid/y/1"])
    answer = judge(repo, [run_of(n) for n in order], wants, "x", 0.1, 300)
    assert answer["passed"] is False
    assert answer["counts"]["passed"] == 0 and answer["counts"]["failed"] == 1
    assert answer["failures"].count("FAILED calc_test.go::TestGrid") == 1
    assert "FAILED calc_test.go::TestGrid/y/1: calc_test.go:11: y/1 was not asked for" in answer["failures"]


def test_a_test_two_runs_disagree_about_is_the_worse_of_the_two_for_a_plain_test_too():
    repo = MODULES / "fail"
    good = run_of("fail", text=stream_text("fail").replace('"Action":"fail","Package":"example.com/fail","Test":"TestBad"', '"Action":"pass","Package":"example.com/fail","Test":"TestBad"'), exit_code=0)
    bad = run_of("fail")
    for runs in ([good, bad], [bad, good]):
        answer = judge(repo, runs, [], "x", 0.1, 300)
        assert answer["counts"]["failed"] == 1 and answer["counts"]["passed"] == 1, answer
        assert answer["failure_count"] == 1 and answer["passed"] is False


def test_a_problem_two_runs_share_is_listed_once():
    repo = MODULES / "silent_exit"
    answer = judge(repo, [run_of("silent_exit_all"), run_of("silent_exit_all")], [], "x", 0.1, 300)
    assert len(answer["problems"]) == len(set(answer["problems"])) == 2


# ---- benchmarks are neither counted nor unfinished ---------------------------------------------


def test_a_benchmark_has_a_run_event_and_never_a_result_so_it_is_not_a_test_that_did_not_finish():
    quiet = parse_stream(stream_text("quiet_bench")).packages["example.com/quiet/benchonly"]
    assert quiet.tests == {"BenchmarkX": "bench"}
    result = verdict("quiet_bench")
    assert not any("did not finish" in f for f in result["failures"]) and result["failures"] == []
    assert not any("never finished" in p or "BenchmarkX" in p for p in result["problems"]), result["problems"]
    assert result["counts"]["passed"] == 2 and result["counts"]["failed"] == 0 and result["counts"]["unreported"] == 0


def test_a_benchmark_that_passes_or_skips_is_not_counted_and_one_that_fails_is_a_failure():
    ok = parse_stream(stream_text("bench_ok")).packages["example.com/bench_ok"]
    assert ok.tests == {"TestOK": "pass", "BenchmarkGood": "bench", "BenchmarkSkipped": "bench"}
    result = verdict("bench_ok")
    assert result["passed"] is True and result["problems"] == [] and result["failures"] == []
    assert result["counts"]["passed"] == 1 and result["counts"]["skipped"] == 0  # not BenchmarkGood, not the skip
    bad = verdict("bench_bad")
    assert meta("bench_bad")["exit_code"] == 1
    assert bad["passed"] is False
    # named by package: a benchmark is in no file `check` can be asked about (it refuses a Benchmark name)
    assert bad["failures"] == ["FAILED example.com/bench_bad::BenchmarkBad: BenchmarkBad"], bad["failures"]
    assert bad["counts"]["failed"] == 1 and bad["counts"]["passed"] == 1


def test_a_test_two_runs_disagree_about_is_the_worse_of_the_two_when_one_ended_with_no_result():
    # ended with no result (the binary exited under it) is worse than a pass, in either order
    repo = MODULES / "exit_early"
    gone = run_of("exit_early")
    package = '"Package":"example.com/exit_early"'
    finished = stream_text("exit_early") + (
        '{"Action":"output",' + package + ',"Output":"PASS\\n"}\n{"Action":"pass",' + package + "}\n"
    )
    for runs in ([gone, run_of("exit_early", text=finished)], [run_of("exit_early", text=finished), gone]):
        answer = judge(repo, runs, [], "x", 0.1, 300)
        assert answer["counts"]["unreported"] == 1 and answer["counts"]["passed"] == 1, answer["counts"]
        assert answer["passed"] is False


def test_under_bench_go_prints_no_notice_so_a_package_with_no_test_is_a_problem_and_a_benchmark_package_is_not():
    # With -bench go leaves out "no tests to run": a helpers-only package then looks exactly like a TestMain that printed
    # PASS and exited 0, which ripple will not call a pass. That is a false fail under an unusual GOFLAGS, never a false pass.
    result = verdict("quiet_bench")
    assert sorted(p.split(":")[0] for p in result["problems"]) == ["example.com/quiet/exnoout", "example.com/quiet/helpers"]
    assert all("no tests to run" in p for p in result["problems"])


# ---- GOFLAGS: what a narrowed run is ----------------------------------------------------------


@pytest.mark.parametrize("name", ["goflags_run", "goflags_test_run", "goflags_quoted_run", "goflags_test_skip", "goflags_list", "goflags_c"])
def test_with_ripples_overrides_go_runs_the_failing_test_whatever_goflags_says(name):
    capture = meta(name)
    assert capture["env"]["GOFLAGS"]  # the flag was in the environment of the run
    assert capture["argv"][:8] == BASE and capture["argv"][8] == "-run="
    assert parse_stream(stream_text(name)).packages["example.com/fail"].tests == {"TestAdd": "pass", "TestBad": "fail"}
    result = verdict(name)
    assert result["passed"] is False
    assert result["failures"] == ["FAILED calc_test.go::TestBad: calc_test.go:13: want 5, got 4"]
    assert any(n.startswith(f"GOFLAGS is set ({capture['env']['GOFLAGS']})") for n in result["notes"])


@pytest.mark.parametrize("name", ["goflags_run_plain", "goflags_test_run_plain", "goflags_quoted_run_plain", "goflags_test_skip_plain"])
def test_a_plain_go_test_under_the_same_goflags_leaves_the_failing_test_out_and_exits_zero(name):
    capture = meta(name)
    assert capture["argv"] == ["go", "test", "-json", "-count=1", "./..."] and capture["exit_code"] == 0
    assert "TestBad" not in parse_stream(stream_text(name)).packages["example.com/fail"].tests


def test_a_plain_go_test_under_list_or_c_runs_nothing_and_exits_zero():
    assert meta("goflags_c_plain")["exit_code"] == 0 and stream_text("goflags_c_plain") == ""
    assert meta("goflags_list_plain")["exit_code"] == 0
    assert parse_stream(stream_text("goflags_list_plain")).packages["example.com/fail"].tests == {}
