"""A section of cargo's output can hold more than one libtest run, and each run is judged (captures, no cargo needed).

The captures are real cargo 1.99.1 output (see tests/fixtures/rs_check/capture.py). The live half is in test_check_rs_live.py.
"""

import re

import pytest
from rs_check_support import meta, output_text, verdict

from ripple.rs_check import parse_output


def said(result, *words):
    return any(all(w in p for w in words) for p in result["problems"])


def doc_section(text, crate):
    [section] = [s for s in parse_output(text).sections if s.doc and s.header == f"doc-tests {crate}"]
    return section


# ---- one section, several libtest runs ---------------------------------------------------------------


def test_the_doc_tests_rustdoc_cannot_merge_on_edition_2024_are_a_second_libtest_run_in_the_same_section():
    # the capture: `running 3 tests` ... `test result:` then, with no `Doc-tests` line between, `running 3 tests` ... `test result:`
    text = output_text("ed24_all")
    section = doc_section(text, "lib24")
    assert len(section.runs) == 2
    first, second = section.runs
    assert (first.declared, second.declared) == (3, 3)
    assert (first.result["passed"], first.result["ignored"]) == (3, 0) and (second.result["passed"], second.result["ignored"]) == (2, 1)
    assert sorted(section.tests.values()) == ["ignored", "ok", "ok", "ok", "ok", "ok"]
    assert text.count("running 3 tests") == 2 and text.count("   Doc-tests lib24") == 1


@pytest.mark.parametrize("name", ["ed24_all", "ed24_pkg"])
def test_a_clean_edition_2024_crate_with_doc_tests_rustdoc_splits_passes_a_whole_run_and_a_package_run(name):
    assert meta(name)["exit_code"] == 0
    result = verdict(name)
    assert result["passed"] is True and result["problems"] == [], result
    assert result["counts"]["passed"] >= 5 and result["counts"]["ignored"] == 1 and result["counts"]["failed"] == 0
    assert result["exit_code"] == 0


# cargo 1.99.1, one captured crate each (tests/fixtures/rs_check/capture.py, DOC_SPLIT): how many libtest runs the `Doc-tests` section has
DOC_RUNS = {
    "docsplit24_plain_plain": 1, "docsplit24_no_run": 1, "docsplit24_ignore": 1, "docsplit24_should_panic": 1, "docsplit24_crate_attr": 1,
    "docsplit24_compile_fail_only": 1, "docsplit24_standalone_only": 1,  # nothing to merge them with
    "docsplit24_compile_fail": 2, "docsplit24_standalone": 2, "docsplit24_ignore_not_rust": 2,
    "docsplit24_edition2015": 2, "docsplit24_edition2018": 2, "docsplit24_edition2021": 2,
    "docsplit21_compile_fail": 1, "docsplit21_standalone": 1, "docsplit21_edition2015": 1,  # edition 2021 merges nothing
}  # fmt: skip


@pytest.mark.parametrize("name, runs", sorted(DOC_RUNS.items()))
def test_every_doc_test_shape_cargo_1_99_splits_or_merges_is_read_and_passes(name, runs):
    text = output_text(name)
    assert text.count("\nrunning ") - 1 == runs  # minus the unit-test binary's own `running 0 tests`
    [section] = [s for s in parse_output(text).sections if s.doc]
    assert len(section.runs) == runs
    assert meta(name)["exit_code"] == 0 and meta(name)["cargo_version"].startswith("cargo 1.99")
    result = verdict(name)
    assert result["passed"] is True and result["problems"] == [], result
    assert result["counts"]["doc_test_crates"] == 1 and result["counts"]["passed"] + result["counts"]["ignored"] == len(section.tests)


def test_the_counts_of_every_libtest_run_of_the_section_are_added_up():
    # one unit test, and 3 + 2 doc tests that ran and 1 that is ignored
    result = verdict("ed24_pkg")
    assert result["summary"] == "6 passed, 0 failed, 1 ignored in 1 test binary and 1 doc-test run"
    whole = verdict("ed24_all")
    assert whole["summary"] == "7 passed, 0 failed, 1 ignored in 2 test binaries and 2 doc-test runs"


@pytest.mark.parametrize("name", ["ed24_fail_standalone", "ed24_fail_merged"])
def test_a_failing_doc_test_in_either_run_of_the_section_fails_the_verdict(name):
    assert meta(name)["exit_code"] == 101
    result = verdict(name)
    assert result["passed"] is False and result["counts"]["failed"] == 1 and result["problems"] == []
    line = "f2 (line 11)" if name.endswith("standalone") else "f0 (line 1)"
    assert [f.split(": ")[0] for f in result["failures"]] == [f"FAILED doctest src/lib.rs - {line}"]
    # the section's other run passed, and it still counts as passed: only the doc test that failed is the failure
    assert result["counts"]["passed"] == 2


def test_the_exit_status_still_takes_the_pass_of_a_split_section_away():
    result = verdict("ed24_all", exit_code=101)
    assert result["passed"] is False and said(result, "exited 101 but its output shows no failure")


def drop(text, pattern, nth):
    """The text without the nth line (0-based) that matches the pattern."""
    hits = [m for m in re.finditer(pattern, text, flags=re.M)]
    assert len(hits) > nth, (pattern, len(hits))
    hit = hits[nth]
    return text[: hit.start()] + text[hit.end() :]


@pytest.mark.parametrize("which, label", [(0, "run 1 of 2"), (1, "run 2 of 2")])
def test_a_missing_result_line_in_either_run_of_the_section_is_a_problem_naming_the_run(which, label):
    text = drop(output_text("ed24_pkg"), r"^test result: ok\. \d+ passed; 0 failed; \d+ ignored.*\n", which + 1)  # the unit-test binary's is the first
    result = verdict("ed24_pkg", text=text)
    assert result["passed"] is False
    assert said(result, "doc-tests lib24", label, "exited without reporting a result"), result["problems"]


def test_each_run_is_judged_by_its_own_lines_not_only_the_section_by_their_sum():
    # run 1 says 2 passed 1 ignored (it has 3 ok), run 2 says 3 passed 0 ignored (it has 2 ok 1 ignored): both runs disagree with
    # their lines but the sums (5 passed, 1 ignored) agree with the lines of the section
    text = output_text("ed24_pkg")
    text = text.replace("test result: ok. 2 passed; 0 failed; 1 ignored", "test result: ok. 3 passed; 0 failed; 0 ignored")  # run 2
    text = text.replace("test result: ok. 3 passed; 0 failed; 0 ignored", "test result: ok. 2 passed; 0 failed; 1 ignored", 1)  # run 1
    assert text.index("2 passed; 0 failed; 1 ignored") < text.index("3 passed; 0 failed; 0 ignored")
    result = verdict("ed24_pkg", text=text)
    assert result["passed"] is False
    assert said(result, "doc-tests lib24", "run 1 of 2", "its result line says 2 passed but 3 tests are listed as such"), result["problems"]
    assert said(result, "doc-tests lib24", "run 2 of 2", "its result line says 3 passed but 2 tests are listed as such"), result["problems"]


def test_each_run_announces_the_tests_it_counts():
    text = output_text("ed24_pkg")
    last = text.rindex("running 3 tests")
    result = verdict("ed24_pkg", text=text[:last] + "running 4 tests" + text[last + len("running 3 tests") :])
    assert result["passed"] is False and said(result, "doc-tests lib24", "run 2 of 2", "announced 4 tests but its result line counts 3")


def test_a_failed_result_line_in_one_run_with_no_failing_test_listed_is_a_problem_in_a_split_section():
    text = output_text("ed24_pkg")
    last = text.rindex("test result: ok. 2 passed")
    cut = text[:last] + text[last:].replace("test result: ok.", "test result: FAILED.", 1)
    result = verdict("ed24_pkg", text=cut)
    assert result["passed"] is False and said(result, "run 2 of 2", "failed with no failing test listed")


def split_binary(extra):
    """The capture of `pass` with `extra` printed straight after the result line of the unit-test binary, in its section."""
    text = output_text("pass")
    first = text.index("test result: ok. 2 passed")
    end = text.index("\n", first) + 1
    return text[:end] + extra + text[end:]


SECOND_RUN_OK = "\nrunning 1 test\ntest tests::third ... ok\n\ntest result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n"


def test_a_unit_test_binary_that_prints_a_second_libtest_run_is_judged_run_by_run_too():
    # a harness that runs libtest twice, or a test binary that runs a nested one: the class is any section, not only doc tests
    section = [s for s in parse_output(split_binary(SECOND_RUN_OK)).sections if not s.doc][0]
    assert [r.declared for r in section.runs] == [2, 1]
    result = verdict("pass", text=split_binary(SECOND_RUN_OK))
    assert result["passed"] is True and result["problems"] == [] and result["counts"]["passed"] == 4


def test_a_failing_test_in_the_second_run_of_a_unit_test_binary_fails_and_a_missing_result_is_a_problem():
    failing = SECOND_RUN_OK.replace("tests::third ... ok", "tests::third ... FAILED").replace("1 passed; 0 failed", "0 passed; 1 failed").replace("test result: ok.", "test result: FAILED.")
    result = verdict("pass", text=split_binary(failing), exit_code=101)
    assert result["passed"] is False and result["counts"]["failed"] == 1 and any("tests::third" in f for f in result["failures"])
    result = verdict("pass", text=split_binary(SECOND_RUN_OK.replace("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n", "")))
    assert result["passed"] is False and said(result, "run 2 of 2", "exited without reporting a result")


def test_a_second_result_line_is_a_run_of_its_own_and_never_replaces_the_first():
    text = split_binary("test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n")
    section = [s for s in parse_output(text).sections if not s.doc][0]
    assert len(section.runs) == 2 and section.runs[0].result["passed"] == 2 and section.runs[1].tests == {}
    result = verdict("pass", text=text)
    assert result["passed"] is False and said(result, "run 2 of 2", "its result line says 2 passed but 0 tests are listed as such")
    result = verdict("pass", text=split_binary("test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n"), exit_code=101)
    assert result["passed"] is False and said(result, "run 2 of 2", "failed with no failing test listed")


def test_a_second_running_line_inside_a_failure_block_is_the_tests_own_output_not_a_run():
    text = output_text("fail")
    section = [s for s in parse_output(text).sections if not s.doc][0]
    assert len(section.runs) == 1
    forged = text.replace("---- tests::bad stdout ----", "---- tests::bad stdout ----\nrunning 9 tests\ntest tests::bad ... ok", 1)
    assert len([s for s in parse_output(forged).sections if not s.doc][0].runs) == 1
    assert verdict("fail", text=forged)["passed"] is False
