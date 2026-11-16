"""Every id a failure line prints is one `check` takes back and runs exactly that test, against the real toolchain.

Skipped, with the reason, when `cargo` is not on PATH. The fixtures and helpers are those of test_check_rs_live.py.
"""

import shutil

import pytest
from rs_check_support import needs_rust_parser
from test_check_rs_live import check, crate, offline_cargo, said  # noqa: F401 (fixtures and helpers of the live tests)

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo is not on PATH")


def ids_of(result):
    return [f[len("FAILED ") :].split(": ", 1)[0] for f in result["failures"] if not f.startswith("FAILED doctest")]


@needs_rust_parser
@pytest.mark.parametrize("name", ["genmod", "rawmod", "macgen", "attrgen", "decorated", "fail", "dupes"])
def test_every_failure_id_a_run_prints_is_accepted_back_and_runs_exactly_that_test(crate, name):
    repo = crate(name)
    ids = ids_of(check(repo))
    assert ids, name
    for ident in ids:
        again = check(repo, [ident])
        assert again["failure_count"] == 1 and again["failures"][0].startswith(f"FAILED {ident}:"), (ident, again)
        assert again["counts"]["passed"] + again["counts"]["failed"] == 1, (ident, again["counts"])


@needs_rust_parser
def test_a_failing_test_in_a_module_a_macro_writes_in_another_file_is_found_by_its_file_and_by_its_id(crate):
    repo = crate("genmod")
    whole = check(repo)
    assert sorted(ids_of(whole)) == ["src/genmod.rs::made::g", "src/lib.rs::here::g"]
    by_file = check(repo, ["src/genmod.rs"])  # the file runs the generated tests under its module
    assert by_file["counts"]["passed"] == 1 and by_file["counts"]["failed"] == 1, by_file
    assert ids_of(by_file) == ["src/genmod.rs::made::g"]
    by_id = check(repo, ["src/genmod.rs::made::g"])
    assert ids_of(by_id) == ["src/genmod.rs::made::g"] and by_id["counts"]["passed"] == 0


@needs_rust_parser
def test_a_typo_in_a_module_of_a_file_with_macros_builds_lists_and_says_no_test_of_that_name(crate):
    result = check(crate("genmod"), ["src/genmod.rs::made_typo::g"])
    assert result["passed"] is False and said(result, "no test named genmod::made_typo::g exists in genmod"), result
    assert result["counts"]["binaries"] == 0  # nothing was run for it


@needs_rust_parser
def test_a_typo_in_a_module_of_a_file_with_no_macros_is_refused_before_cargo_runs(crate, monkeypatch):
    monkeypatch.setenv("PATH", "")  # a refusal made before any process is started does not need cargo
    with pytest.raises(ValueError, match="src/nomacro.rs has no module 'tpyo'"):
        check(crate("genmod"), ["src/nomacro.rs::tpyo::fine"])
