"""The verdict `check` gives for each captured `cargo test` run (real output, no cargo needed).

Each outcome class has a capture under tests/fixtures/rs_check/captures (the crate and the exact command that made it are
beside it, see capture.py). A guard that turns a would-be pass into a problem has a test here that fails when the guard goes.
"""

import re

import pytest
from rs_check_support import meta, needs_rust_parser, output_text, repo_of, run_of, verdict

from ripple.rs_check import Model, Pick, parse_output


def problems(name, **kw):
    return verdict(name, **kw)["problems"]


def said(result, *words):
    return any(all(w in p for w in words) for p in result["problems"])


# ---- what each outcome class comes out as ------------------------------------------------------


def test_a_passing_run_passes_and_counts_unit_tests_and_doc_tests():
    result = verdict("pass")
    assert result["passed"] is True and result["problems"] == [] and result["exit_code"] == 0
    assert result["counts"]["passed"] == 3
    assert result["summary"] == "3 passed, 0 failed, 0 ignored in 1 test binary and 1 doc-test run"


def test_a_failing_test_is_named_by_the_id_check_accepts_with_where_it_panicked():
    result = verdict("fail")
    assert result["passed"] is False
    assert result["failures"] == ["FAILED src/lib.rs::tests::bad: panicked at src/lib.rs:14:9: assertion `left == right` failed"]
    assert result["failure_count"] == 1 and result["problems"] == []


@needs_rust_parser
def test_a_failure_in_a_module_file_is_named_by_the_file_it_is_in_not_the_crate_root():
    # the test is `tests::t_util` inside src/util.rs: the name libtest prints is `util::tests::t_util`
    result = verdict("mixed_util_file", text=output_text("mixed_util_file").replace("test util::tests::t_util ... ok", "test util::tests::t_util ... FAILED"))
    assert any(f.startswith("FAILED src/util.rs::tests::t_util") for f in result["failures"]), result


@pytest.mark.parametrize(
    "name, words",
    [
        ("build_failure", ("build failed", "cannot find value `missing`", "src/lib.rs:2")),
        ("test_build_failure", ("build failed", "mismatched types", "src/lib.rs:10")),
        ("build_script_failure", ("build failed", "failed to run custom build command")),
        ("missing_dependency", ("build failed", "no matching package named `ripple_no_such_crate_xyz`")),
    ],
)
def test_a_build_failure_is_not_a_pass_and_says_what_cargo_said(name, words):
    result = verdict(name)
    assert result["passed"] is False
    assert said(result, *words), result["problems"]


@pytest.mark.parametrize("name", ["build_failure", "test_build_failure", "build_script_failure", "missing_dependency"])
def test_a_build_failure_is_not_a_pass_even_if_the_exit_status_said_zero(name):
    # the events are the witness, the exit status only a second one
    result = verdict(name, exit_code=0)
    assert result["passed"] is False and result["problems"]


def test_a_test_target_that_does_not_build_while_the_library_does_is_a_build_failure():
    # the lib builds for the integration tests but not in the test profile: the build still failed
    result = verdict("test_build_failure")
    assert said(result, "build failed") and result["counts"]["binaries"] == 0


def test_running_zero_tests_is_not_a_pass():
    result = verdict("no_tests")
    assert result["passed"] is False and said(result, "no test ran"), result
    assert verdict("no_tests", exit_code=0)["passed"] is False


@needs_rust_parser
def test_a_name_the_binary_does_not_list_is_no_test_of_that_name_not_a_filter_that_matched_nothing():
    # gated: the test is behind a feature that is off. The binary lists one test, so `tests::slow` does not exist, and nothing is run
    assert meta("gated_slow")["listed"] is True
    result = verdict("gated_slow")
    assert result["passed"] is False
    assert said(result, "no test named tests::slow exists in gated", "its test binary lists 1 test")
    assert result["counts"]["binaries"] == 0  # no run was made for it


def test_a_filter_that_ran_and_matched_nothing_is_still_not_a_pass_when_no_list_was_asked_for():
    # a hand-made run (no list behind it): the `running 0 tests ... 1 filtered out` of the capture is exit 0 and no result for the name
    result = verdict("pass_nope")
    assert result["passed"] is False and said(result, "no test named tests::nope ran", "the filter matched nothing")


def test_a_name_that_is_in_no_source_at_all_is_not_a_pass_either():
    picks = [Pick("tests::nope", "src/lib.rs", "lib", ("tests::nope",))]
    result = verdict("pass_nope", runs=[run_of("pass_nope", picks=picks)])
    assert result["passed"] is False and said(result, "tests::nope")


def test_the_names_a_run_was_made_with_are_asked_for_even_when_no_request_made_them():
    # a hand-made command has names and no request behind it: the run still has to show each of them
    result = verdict("pass_nope")
    assert result["passed"] is False and said(result, "no test named tests::nope ran")


@needs_rust_parser
def test_an_ignored_test_that_was_asked_for_is_not_a_pass():
    result = verdict("ignored_asked")
    assert result["passed"] is False and said(result, "tests::slow", "#[ignore]")
    assert meta("ignored_asked")["exit_code"] == 0


def test_ignored_tests_in_a_whole_run_pass_and_are_counted_and_named_in_a_note():
    result = verdict("ignored_all")
    assert result["passed"] is True and result["counts"]["ignored"] == 2
    assert any("2 ignored tests were not run" in n for n in result["notes"])


def test_a_doctest_failure_fails_and_is_listed_as_a_doctest():
    result = verdict("doctest_failure")
    assert result["passed"] is False
    assert result["failures"][0].startswith("FAILED doctest src/lib.rs - add (line 3): panicked at src/lib.rs:5:1")
    assert result["counts"]["passed"] == 1 and result["counts"]["failed"] == 1


def test_a_binary_that_exits_early_with_status_zero_is_not_a_pass():
    # `std::process::exit(0)` in a test: cargo exits 0, the doc tests after it pass, and the unit tests never report
    assert meta("exit_early")["exit_code"] == 0
    result = verdict("exit_early")
    assert result["passed"] is False
    assert said(result, "exited without reporting a result", "running 2 tests", "1 reported")
    assert result["counts"]["unreported"] == 1


@pytest.mark.parametrize("name, how", [("abort", "SIGABRT"), ("killed", "SIGKILL")])
def test_a_binary_that_aborts_or_is_killed_is_not_a_pass(name, how):
    result = verdict(name)
    assert result["passed"] is False and said(result, "the test binary died", how, "after 1 test reported of 2"), result


def test_a_test_target_with_its_own_harness_that_panics_is_not_a_pass():
    result = verdict("own_harness_panic")
    assert result["passed"] is False
    assert said(result, "own_harness_panic/it", "its own test harness failed", "the setup of this test is broken")


def test_a_test_target_with_its_own_harness_that_exits_zero_passes_on_its_exit_status_and_says_so():
    result = verdict("own_harness_ok")
    assert result["passed"] is True and result["counts"]["own_harness"] == 1
    assert any("harness = false" in n and "exit status alone" in n for n in result["notes"])
    # cargo prints no `running N tests` for it: without the manifest saying libtest is off, that is a binary that said nothing
    text = output_text("own_harness_ok")
    assert verdict("own_harness_ok", text=text)["passed"] is True


def test_ripples_own_timeout_is_a_timeout_not_a_pass():
    result = verdict("timeout")
    assert result["passed"] is False and result["timed_out"] is True and result["exit_code"] is None
    assert said(result, "timed out after 12s") and said(result, "still running when it was killed")


# ---- the cross-checks, each of which fails the run when it is removed ------------------------------


def test_cargos_exit_status_can_take_a_pass_away():
    result = verdict("pass", exit_code=101)
    assert result["passed"] is False and said(result, "exited 101 but its output shows no failure")


def test_a_missing_result_line_for_a_binary_is_a_problem():
    text = output_text("pass")
    cut = re.sub(r"test result: ok\. 2 passed.*\n", "", text, count=1)
    assert cut != text
    result = verdict("pass", text=cut)
    assert result["passed"] is False and said(result, "exited without reporting a result")


def test_one_package_of_a_workspace_run_that_exits_without_a_result_fails_the_run_the_other_passed_in():
    text = output_text("dupes_all")
    first = text.index("test result: ok. 2 passed")  # package a: two tests, which passed
    cut = text[:first] + text[text.index("\n", first) + 1 :]
    result = verdict("dupes_all", text=cut.replace("test it_works ... FAILED", "test it_works ... ok"), exit_code=0)
    assert result["passed"] is False and said(result, "a/a: the test binary exited without reporting a result")


def test_a_test_binary_the_json_says_was_built_but_that_never_ran_is_a_problem():
    text = output_text("dupes_all")
    start = text.index("     Running unittests src/lib.rs (")
    end = text.index("   Doc-tests a")
    result = verdict("dupes_all", text=text[:start] + text[end:])
    assert result["passed"] is False and said(result, "was built but never run")


def test_the_counts_of_a_result_line_must_match_the_tests_listed():
    text = output_text("pass").replace("test result: ok. 2 passed", "test result: ok. 3 passed")
    result = verdict("pass", text=text)
    assert result["passed"] is False and said(result, "its result line says 3 passed but 2 tests are listed")


def test_a_binary_that_announces_more_tests_than_it_counts_is_a_problem():
    text = output_text("pass").replace("running 2 tests", "running 5 tests")
    result = verdict("pass", text=text)
    assert result["passed"] is False and said(result, "announced 5 tests")


def test_a_result_line_that_says_ok_beside_a_failing_test_is_a_problem():
    text = output_text("fail").replace("test result: FAILED. 1 passed; 1 failed", "test result: ok. 1 passed; 1 failed")
    assert verdict("fail", text=text)["passed"] is False


def test_a_failed_result_line_with_no_failing_test_listed_is_a_problem():
    text = output_text("pass").replace("test result: ok. 2 passed", "test result: FAILED. 2 passed")
    assert said(verdict("pass", text=text), "failed with no failing test listed")


@needs_rust_parser
def test_a_run_with_no_test_binary_at_all_for_the_named_test_is_not_a_pass():
    text = output_text("pass_adds")
    start = text.index("     Running unittests")
    result = verdict("pass_adds", text=text[:start])
    assert result["passed"] is False


def test_ansi_colour_in_the_output_does_not_hide_a_result():
    text = output_text("pass").replace("test tests::adds ... ok", "test tests::adds ... \x1b[32mok\x1b[0m")
    assert verdict("pass", text=text)["passed"] is True


# ---- a run is never wider or narrower than asked ---------------------------------------------------


def test_a_substring_filter_that_ran_a_second_test_is_not_a_pass_for_the_one_asked():
    # `adds` as a substring also runs `adds_zero`: the capture is that run, and asked for one test it is a problem
    picks = [Pick("src/lib:tests::adds", "src/lib.rs", "lib", ("tests::adds",))]
    result = verdict("pass_substring", runs=[run_of("pass_substring", picks=picks)])
    assert result["passed"] is False and said(result, "1 test ran that was not asked for", "tests::adds_zero")


@needs_rust_parser
def test_the_exact_run_of_a_name_that_is_a_prefix_of_another_runs_only_it():
    picks = [Pick("src/lib:tests::adds", "src/lib.rs", "lib", ("tests::adds",))]
    result = verdict("pass_adds", runs=[run_of("pass_adds", picks=picks)])
    assert result["passed"] is True and result["counts"]["passed"] == 1


@needs_rust_parser
def test_two_packages_with_a_test_of_the_same_name_a_run_for_one_does_not_count_the_others():
    assert verdict("dupes_a")["passed"] is True  # a's it_works passes
    result = verdict("dupes_b")  # b's fails, and only b's was run
    assert result["passed"] is False and result["counts"]["failed"] == 1 and result["counts"]["passed"] == 0
    both = verdict("dupes_all")
    assert both["counts"]["passed"] == 2 and both["counts"]["failed"] == 1  # a's two and b's one, all in the whole run


def test_a_substring_filter_across_a_workspace_runs_both_same_named_tests_and_the_verdict_for_a_would_be_a_failure():
    # `-- it_works` with no --exact in a workspace: each package's it_works runs, and one asked for alone is not what ran
    picks = [Pick("a/src/lib:tests::it_works", "a/src/lib.rs", "lib", ("tests::it_works",))]
    result = verdict("dupes_substring", runs=[run_of("dupes_substring", picks=picks)])
    assert result["passed"] is False


@needs_rust_parser
def test_a_whole_target_ask_needs_a_test_to_have_run_in_it():
    picks = [Pick("tests/it.rs", "tests/it.rs", "test", None)]
    result = verdict("mixed_it_file", runs=[run_of("mixed_it_file", picks=picks)])
    assert result["passed"] is True and result["counts"]["passed"] == 3
    empty = output_text("mixed_it_file").replace("test common::t_common ... ok\n", "").replace("test same ... ok\n", "").replace("test t_it ... ok\n", "")
    empty = empty.replace("running 3 tests", "running 0 tests").replace("test result: ok. 3 passed", "test result: ok. 0 passed")
    result = verdict("mixed_it_file", text=empty, runs=[run_of("mixed_it_file", text=empty, picks=picks)])
    assert result["passed"] is False and said(result, "no test ran there")


# ---- nothing-ran is not a pass, per binary and per run, in every shape of run -------------------------


def test_a_test_target_that_ran_no_test_fails_a_run_in_which_the_library_ran_some():
    # the unit tests ran and passed (1 passed): the integration test file beside them holds no test, and that is not a pass
    result = verdict("empty_target")
    assert result["passed"] is False and result["counts"]["passed"] == 1
    assert said(result, "empty_target/empty", "the test target ran no test")
    assert meta("empty_target")["exit_code"] == 0


@needs_rust_parser
def test_a_run_that_ran_nothing_fails_a_verdict_whose_other_run_ran_tests():
    # two runs in one verdict (a package each): the first passed, the second ran no test; the verdict must not pass
    result = verdict("pass_adds", runs=[run_of("pass_adds"), run_of("no_tests")])
    assert result["passed"] is False and said(result, "no test ran")
    assert verdict("pass_adds", runs=[run_of("pass_adds"), run_of("pass_both")])["passed"] is True  # the same shape, both ran


def test_a_workspace_run_in_which_every_package_ran_a_test_passes_and_one_binary_without_tests_is_counted():
    result = verdict("mixed_all")
    assert result["passed"] is True and result["counts"]["empty_binaries"] == 0
    # an ordinary bin with no unit tests is not a failure, whatever else ran: it is counted
    text = output_text("mixed_all")
    bin_start = text.index("     Running unittests src/main.rs")
    bin_end = text.index("     Running tests/it.rs")
    emptied = text[:bin_start] + re.sub(
        r"test result: ok\. 2 passed", "test result: ok. 0 passed", text[bin_start:bin_end].replace("running 2 tests", "running 0 tests").replace("test tests::same ... ok\n", "").replace("test tests::t_bin ... ok\n", "")
    ) + text[bin_end:]
    result = verdict("mixed_all", text=emptied)
    assert result["passed"] is True and result["counts"]["empty_binaries"] == 1


def test_only_ignored_tests_ran_is_not_a_pass_of_a_whole_run():
    text = output_text("ignored_all").replace("test tests::adds ... ok", "test tests::adds ... ignored").replace("1 passed; 0 failed; 2 ignored", "0 passed; 0 failed; 3 ignored")
    text = text.replace("running 3 tests", "running 3 tests")
    result = verdict("ignored_all", text=text)
    assert result["passed"] is False and said(result, "every test that ran was ignored")


# ---- the environment ----------------------------------------------------------------------------------


def test_quiet_verbose_and_colour_in_the_environment_do_not_change_what_is_read():
    # the run was made with CARGO_TERM_QUIET, CARGO_TERM_VERBOSE and CARGO_TERM_COLOR=always set (the argv outranks them)
    assert meta("pass_noisy_env")["env"]["CARGO_TERM_QUIET"] == "true"
    result = verdict("pass_noisy_env")
    assert result["passed"] is True and result["counts"]["passed"] == 3
    assert "\x1b[" not in output_text("pass_noisy_env")


def test_libtests_nocapture_in_the_environment_is_never_read_as_a_pass_it_cannot_see():
    # RUST_TEST_NOCAPTURE puts a test's output in front of libtest's own line: the test is not listed, and the counts say so
    assert meta("printer_nocapture")["exit_code"] == 0
    result = verdict("printer_nocapture")
    assert result["passed"] is False and said(result, "its result line says 2 passed but 1 tests are listed")
    assert verdict("printer")["passed"] is True  # the same crate, captured


# ---- reading the output ---------------------------------------------------------------------------


def test_the_sections_follow_cargos_running_lines_and_the_json_names_each_binary():
    out = parse_output(output_text("mixed_all"))
    assert [s.header for s in out.sections] == ["unittests src/lib.rs", "unittests src/main.rs", "tests/it.rs", "tests/other.rs", "doc-tests mixed"]
    assert out.build_finished is True and len(out.artifacts) == 4
    assert {a.name for a in out.artifacts.values()} == {"mixed", "it", "other"}
    assert out.sections[0].tests == {"tests::same": "ok", "net::tests::t_net": "ok", "util::tests::t_util": "ok", "tests::t_lib": "ok"}


def test_a_forged_cargo_line_in_a_failure_block_cannot_make_a_failing_run_pass():
    text = output_text("fail").replace("left: 3", "     Running unittests src/lib.rs (/x/y)\ntest bogus ... ok\n  left: 3")
    # cargo's exit status and the result line still say it failed
    assert verdict("fail", text=text)["passed"] is False


def test_the_captures_are_of_the_toolchain_the_tests_were_written_against():
    assert meta("pass")["cargo_version"].startswith("cargo 1.")
    assert repo_of("pass").is_dir()


# ---- what a file or directory covers is the binary's list, never a reading of the source ----------------


@pytest.mark.parametrize("name", ["macgen_file", "macgen_dir", "attrgen_file", "macgen_src_dir"])
@needs_rust_parser
def test_a_failing_test_a_macro_writes_in_the_file_asked_for_fails_the_run(name):
    # the test is no function in the file (macro_rules in another file, or an attribute macro that adds tests): the list has it
    result = verdict(name)
    assert result["passed"] is False
    assert any("generated" in f or "broken" in f for f in result["failures"]), result["failures"]


@needs_rust_parser
def test_the_names_of_a_file_run_come_from_the_binarys_list_and_include_the_generated_tests():
    run = run_of("macgen_file")
    assert run.invocation.names == ("util::tests::generated_fails", "util::tests::plain")
    assert run.resolution.total == 4 and run.resolution.expected == {0: run.invocation.names}
    result = verdict("macgen_file")
    assert result["failures"] == ["FAILED src/util.rs::tests::generated_fails: panicked at src/util.rs:8:5: assertion `left == right` failed"]
    assert result["counts"] == {**result["counts"], "passed": 1, "failed": 1}


@needs_rust_parser
def test_an_attribute_macros_tests_are_run_and_named_by_the_file_they_were_written_in():
    result = verdict("attrgen_file")
    assert {"broken_a", "broken_b"} == {f.split("::")[-1].split(":")[0] for f in result["failures"]}
    assert all(f.startswith("FAILED attrgen/src/util.rs::tests::broken_") for f in result["failures"])
    assert run_of("attrgen_file").invocation.names[:2] == ("util::tests::broken_a", "util::tests::broken_b")


@needs_rust_parser
def test_a_directory_below_the_crate_root_is_every_test_listed_below_its_modules():
    result = verdict("macgen_dir")
    assert result["counts"]["passed"] == 1 and result["counts"]["failed"] == 1
    assert result["failures"][0].startswith("FAILED src/sub/mod.rs::tests::sub_generated_fails")


@needs_rust_parser
def test_a_listed_test_with_no_result_of_its_own_is_a_problem():
    text = output_text("macgen_file").replace("test util::tests::plain ... ok\n", "")
    text = text.replace("test result: FAILED. 1 passed; 1 failed", "test result: FAILED. 0 passed; 1 failed").replace("running 2 tests", "running 1 test")
    result = verdict("macgen_file", text=text)
    assert result["passed"] is False and said(result, "below it have no result of their own", "util::tests::plain")


@needs_rust_parser
def test_a_test_that_ran_outside_the_selection_is_a_problem():
    text = output_text("macgen_file").replace("test util::tests::plain ... ok", "test util::tests::plain ... ok\ntest sub::tests::sub_plain ... ok")
    result = verdict("macgen_file", text=text)
    assert result["passed"] is False and said(result, "1 test ran that was not asked for", "sub::tests::sub_plain")


@needs_rust_parser
def test_an_id_the_list_does_not_have_is_no_test_of_that_name_even_beside_ones_that_exist():
    result = verdict("macgen_missing_id")
    assert result["passed"] is False and said(result, "no test named util::tests::not_a_test exists in macgen", "lists 4 tests")
    assert result["counts"]["binaries"] == 0


@needs_rust_parser
def test_a_generated_test_asked_for_by_id_runs_by_its_listed_name():
    result = verdict("macgen_id")
    assert result["passed"] is False and result["failures"][0].startswith("FAILED src/util.rs::tests::generated_fails")


# ---- libtest's grammar: the decorations between a test's name and its result ---------------------------


@needs_rust_parser
def test_a_should_panic_test_asked_for_by_id_passes():
    for name in ("decorated_sp", "decorated_sp_msg"):
        result = verdict(name)
        assert result["passed"] is True and result["counts"]["passed"] == 1, (name, result["problems"])
    assert "test tests::sp - should panic ... ok" in output_text("decorated_sp")


@needs_rust_parser
def test_a_should_panic_test_that_did_not_panic_fails_under_its_own_name_with_the_reason():
    result = verdict("decorated_sp_no_panic")
    assert result["passed"] is False
    assert result["failures"] == ["FAILED src/lib.rs::tests::sp_no_panic: note: test did not panic as expected at src/lib.rs:48:8"]


def test_every_decoration_libtest_writes_is_read_in_the_unit_tests_and_the_doc_tests():
    out = parse_output(output_text("decorated"))
    unit, doc = [s for s in out.sections if not s.doc][0], [s for s in out.sections if s.doc][0]
    assert unit.unparsed == [] and doc.unparsed == []
    assert unit.tests == {
        "tests::ign_reason": "ignored", "tests::plain": "ok", "tests::sp_ignored": "ignored", "tests::r#match": "ok",
        "tests::sp": "ok", "tests::sp_msg": "ok", "tests::sp_no_panic": "FAILED", "tests::sp_wrong_msg": "FAILED",
    }  # fmt: skip
    assert doc.tests == {
        "src/lib.rs - f (line 19)": "ignored", "src/lib.rs - f (line 11)": "ok", "src/lib.rs - f (line 7)": "ok",
        "src/lib.rs - f (line 15)": "ok", "src/lib.rs - f (line 3)": "ok", "src/lib.rs - f (line 23)": "FAILED",
    }  # fmt: skip
    text = output_text("decorated")
    for line in ("test tests::sp - should panic ... ok", "test src/lib.rs - f (line 11) - compile ... ok", "test src/lib.rs - f (line 7) - compile fail ... ok", "test tests::ign_reason ... ignored, needs a database"):
        assert line in text


def test_the_failures_of_a_run_with_decorated_tests_are_named_and_counted():
    result = verdict("decorated")
    assert result["passed"] is False and result["counts"]["passed"] == 8 + 0 and result["counts"]["failed"] == 3
    assert sorted(f.split(": ")[0] for f in result["failures"]) == [
        "FAILED doctest src/lib.rs - f (line 23)", "FAILED src/lib.rs::tests::sp_no_panic", "FAILED src/lib.rs::tests::sp_wrong_msg",
    ]  # fmt: skip
    assert result["problems"] == []


@needs_rust_parser
def test_a_decoration_ripple_does_not_know_is_a_problem_naming_the_line_never_a_silent_mismatch():
    text = output_text("decorated_sp").replace("test tests::sp - should panic ... ok", "test tests::sp - should wobble ... ok")
    result = verdict("decorated_sp", text=text)
    assert result["passed"] is False
    assert said(result, "libtest printed a test line ripple cannot read", "test tests::sp - should wobble ... ok")


def test_a_bench_line_is_a_problem_too_a_stable_compiler_prints_none():
    text = output_text("pass").replace("test tests::adds ... ok", "test tests::adds ... bench:         1 ns/iter (+/- 0)")
    result = verdict("pass", text=text)
    assert result["passed"] is False and said(result, "ripple cannot read", "bench:")


@needs_rust_parser
def test_a_raw_identifier_test_is_asked_for_by_its_listed_name():
    assert "tests::r#match: test" in list_text_of("decorated_raw")
    result = verdict("decorated_raw")
    assert result["passed"] is True and result["counts"]["passed"] == 1
    assert run_of("decorated_raw").invocation.names == ("tests::r#match",)


def list_text_of(name):
    from rs_check_support import list_text

    return list_text(name)


def test_a_raw_identifier_is_found_whichever_way_it_is_written():
    from ripple.rs_check import Invocation, resolve

    inv = Invocation("", [], (), [Pick("g", "src/lib.rs", "lib", ("tests::match",))], None, True)
    res = resolve(inv, ["tests::plain", "tests::r#match"])
    assert res.names == ("tests::r#match",) and res.problems == []
    inv = Invocation("", [], (), [Pick("g", "src/lib.rs", "lib", None, (("r#try",),))], None, True)
    assert resolve(inv, ["r#try::x", "other::x"]).names == ("r#try::x",)


# ---- a helper file of tests/, an example, a binary that exits before libtest, a forged result ----------


@needs_rust_parser
def test_a_helper_file_another_test_target_reads_as_a_module_is_a_note_not_a_failed_run():
    result = verdict("helper_all")
    assert result["passed"] is True and result["problems"] == []
    assert any("helper/util (tests/util.rs) holds no test" in n and "helper" in n for n in result["notes"])
    assert result["counts"]["empty_binaries"] == 2  # the lib has no unit tests, the helper holds none


def test_a_test_target_with_no_test_that_nothing_else_reads_is_still_not_a_pass():
    assert verdict("empty_target")["passed"] is False


@needs_rust_parser
def test_an_example_with_a_test_is_run_with_example_and_passes():
    for name in ("example_file", "example_id"):
        result = verdict(name)
        assert result["passed"] is True and result["counts"]["passed"] == 1, name
        assert meta(name)["argv"][-2:] == ["--example", "demo"] or "--example" in meta(name)["argv"]


def test_a_binary_that_exits_zero_before_libtest_starts_is_not_a_pass():
    # `std::process::exit(0)` from an .init_array constructor: cargo exits 0, the lib's test passes, and the test that would fail never runs
    assert meta("pre_all")["exit_code"] == 0
    result = verdict("pre_all")
    assert result["passed"] is False
    assert said(result, "pre/early", "the test binary printed no libtest output and no result")
    assert result["counts"]["passed"] == 1


@needs_rust_parser
def test_a_binary_that_exits_before_libtest_starts_has_no_list_so_a_named_test_in_it_does_not_exist():
    result = verdict("pre_early")
    assert result["passed"] is False and said(result, "would_fail")


@needs_rust_parser
def test_a_test_that_forges_libtests_lines_and_exits_zero_is_caught_where_a_list_is_the_witness():
    # asked for by id, the binary lists both tests and the result line must say 1 filtered out: the forged one says 0
    result = verdict("forge_a")
    assert result["passed"] is False
    assert said(result, "its result line says 0 tests were filtered out", "the binary lists 2 and 1 were selected")
    assert said(result, "1 test ran that was not asked for", "b")
    assert said(result, "announced 1 tests but its result line counts 2")


def test_the_same_forgery_in_a_whole_run_reads_as_passed_because_stable_cargo_gives_no_structured_results():
    # what remains: with no list to compare with, a binary that imitates libtest's output and exits 0 is read as passed
    assert meta("forge_all")["exit_code"] == 0
    result = verdict("forge_all")
    assert result["passed"] is True and result["counts"]["passed"] == 2


def test_the_mutation_of_the_pre_harness_guard_is_visible_only_in_that_problem():
    # the only witness for the early exit is the missing libtest output: the exit status is 0 and the other binary passed
    assert meta("pre_all")["exit_code"] == 0 and verdict("pre_all")["problems"] == [
        "pre/early: the test binary printed no libtest output and no result"
    ]


def test_an_executable_path_with_parentheses_is_still_a_section():
    text = output_text("pass").replace("@TARGET@", "/tmp/x (copy)/target")
    text = text.replace(TARGET_IN_PASS, "/tmp/x (copy)/target") if TARGET_IN_PASS in text else text
    out = parse_output(text.replace("/tmp/ripple-rs-check-target", "/tmp/x (copy)/target"))
    assert out.sections and all(sec.exe for sec in out.sections if not sec.doc)
    assert verdict("pass", text=text.replace("/tmp/ripple-rs-check-target", "/tmp/x (copy)/target"))["passed"] is True


TARGET_IN_PASS = "/tmp/ripple-rs-check-target"
