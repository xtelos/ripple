"""A section of cargo's output with two libtest runs (rustdoc's doc tests on edition 2024), against the real toolchain.

Skipped, with the reason, when `cargo` is not on PATH. The fixtures and helpers are those of test_check_rs_live.py.
"""

import shutil

import pytest
from rs_check_support import needs_rust_parser
from test_check_rs_live import check, crate, offline_cargo  # noqa: F401 (fixtures and helpers of the live tests)

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo is not on PATH")


def test_a_clean_edition_2024_crate_whose_doc_tests_rustdoc_splits_passes_with_no_arguments(crate):
    # compile_fail and standalone_crate doc tests are a second libtest run under the same `Doc-tests` line
    result = check(crate("ed24"))
    assert result["passed"] is True and result["problems"] == [], result
    assert result["exit_code"] == 0 and result["counts"]["failed"] == 0 and result["counts"]["ignored"] == 1
    assert result["counts"]["passed"] == 7  # one unit test of each package and five doc tests that ran


@needs_rust_parser
def test_a_clean_edition_2024_crate_passes_a_run_of_the_package_too(crate):
    result = check(crate("ed24"), ["lib24"])
    assert result["passed"] is True and result["problems"] == [], result
    assert result["command"].endswith("-p lib24") and result["counts"]["passed"] == 6


@pytest.mark.parametrize("name, line", [("ed24_fail_standalone", "src/lib.rs - f2 (line 11)"), ("ed24_fail_merged", "src/lib.rs - f0 (line 1)")])
def test_a_failing_doc_test_in_either_run_of_an_edition_2024_section_fails(crate, name, line):
    result = check(crate(name))
    assert result["passed"] is False and result["exit_code"] == 101 and result["counts"]["failed"] == 1
    assert [f.split(": ")[0] for f in result["failures"]] == [f"FAILED doctest {line}"]
