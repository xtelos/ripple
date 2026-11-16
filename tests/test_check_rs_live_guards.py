"""The all-ignored and vanished-test guards, raw identifier ids and the whole-target forgery against the real toolchain.

Skipped, with the reason, when `cargo` is not on PATH. The fixtures and helpers are those of test_check_rs_live.py.
"""

import shutil

import pytest
from rs_check_support import needs_rust_parser
from test_check_rs_live import check, crate, offline_cargo, said  # noqa: F401 (fixtures and helpers of the live tests)

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo is not on PATH")


@needs_rust_parser
def test_a_file_whose_every_test_is_ignored_is_not_a_pass(crate):
    repo = crate("allig")
    result = check(repo, ["src/util.rs"])
    assert result["passed"] is False and result["exit_code"] == 0 and result["counts"]["ignored"] == 2, result
    assert said(result, "every test below it is marked #[ignore], so none ran")
    assert check(repo, ["src/lib.rs::tests::fine"])["passed"] is True  # the rest of the crate is fine: only the file asked for is empty


@needs_rust_parser
def test_a_test_that_is_gone_between_the_list_and_the_run_is_not_a_pass(crate, monkeypatch):
    # libtest then runs nothing, prints `0 passed ... 1 filtered out` (which is what the shorter list leaves over) and exits 0
    from ripple import rs_check

    repo = crate("pass")
    real = rs_check.resolve

    def resolve_then_delete_the_test(invocation, listed):
        resolution = real(invocation, listed)
        source = repo / "src" / "lib.rs"
        text = source.read_text()
        gone = "    #[test]\n    fn adds() {\n        assert_eq!(add(1, 2), 3);\n    }\n"
        assert gone in text
        source.write_text(text.replace(gone, ""))
        return resolution

    monkeypatch.setattr(rs_check, "resolve", resolve_then_delete_the_test)
    result = check(repo, ["src/lib:tests::adds"])
    assert result["passed"] is False and result["exit_code"] == 0, result
    assert said(result, "tests::adds is listed but has no result of its own")


@needs_rust_parser
def test_every_failure_id_in_a_raw_identifier_module_is_one_check_takes_back(crate):
    repo = crate("rawmod")
    whole = check(repo)
    assert whole["passed"] is False and whole["failure_count"] == 3
    ids = [f[len("FAILED ") :].split(": ", 1)[0] for f in whole["failures"]]
    assert sorted(ids) == ["src/lib.rs::r#in::tests::r#fn", "src/type.rs::tests::r#match", "src/type.rs::tests::t_fails"]
    for ident in ids:
        again = check(repo, [ident])
        assert again["passed"] is False and again["failure_count"] == 1, (ident, again)
        assert again["failures"][0].startswith(f"FAILED {ident}:"), again["failures"]
    by_file = check(repo, ["src/type.rs"])
    assert sorted(f.split(": ")[0] for f in by_file["failures"]) == ["FAILED src/type.rs::tests::r#match", "FAILED src/type.rs::tests::t_fails"]


@needs_rust_parser
def test_a_file_that_is_a_whole_test_target_has_no_list_so_the_forgery_in_it_reads_as_passed(crate):
    repo = crate("forge")
    forged = check(repo, ["tests/it.rs"])
    assert forged["passed"] is True and forged["counts"]["passed"] == 2 and forged["command"].endswith("--test it")
    assert check(repo, ["tests/it:a"])["passed"] is False  # an id has a list to compare with
