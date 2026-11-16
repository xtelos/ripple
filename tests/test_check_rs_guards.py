"""The guards that stop an all-ignored or vanished test from passing, ids that must come back, and the forgery limit (captures, no cargo needed).

The captures are real cargo 1.99.1 output (see tests/fixtures/rs_check/capture.py). The live half is in test_check_rs_live_guards.py.
"""

import pytest
from rs_check_support import meta, needs_rust_parser, output_text, repo_of, run_of, verdict

from ripple.rs_check import Model, normalize, parse_wants


# ---- the two guards that stop an all-ignored pass, when a file or directory is asked for ------------


@needs_rust_parser
def test_a_file_whose_every_test_is_ignored_is_not_a_pass_though_cargo_exits_zero():
    # the binary lists util::tests::slow and slower, both #[ignore]: libtest runs none of them and exits 0
    assert meta("allig_file")["exit_code"] == 0
    result = verdict("allig_file")
    assert result["passed"] is False and result["exit_code"] == 0
    assert result["counts"]["passed"] == 0 and result["counts"]["ignored"] == 2
    assert result["problems"] == ["src/util.rs: every test below it is marked #[ignore], so none ran"]


@needs_rust_parser
def test_one_ignored_test_beside_a_test_that_ran_is_a_note_not_a_problem():
    text = output_text("allig_file").replace("test util::tests::slow ... ignored", "test util::tests::slow ... ok").replace(
        "0 passed; 0 failed; 2 ignored", "1 passed; 0 failed; 1 ignored"
    )
    result = verdict("allig_file", text=text)
    assert result["passed"] is True and result["problems"] == []
    assert any("1 test below it are marked #[ignore] and were not run" in n for n in result["notes"])


@needs_rust_parser
def test_a_listed_test_that_is_gone_when_the_run_starts_is_a_problem_not_a_filtered_out_pass():
    # `tests::adds` is renamed or removed between the `--list` call and the run: libtest runs nothing, prints `0 passed ... 1 filtered out`
    # (the new total is 1, the old list's 2 minus the 1 selected), and exits 0, so only the missing result of its own says so
    text = output_text("pass_adds").replace("running 1 test\ntest tests::adds ... ok\n", "running 0 tests\n").replace("1 passed; 0 failed", "0 passed; 0 failed")
    assert "running 0 tests" in text and "0 passed" in text and "1 filtered out" in text
    result = verdict("pass_adds", text=text)
    assert result["passed"] is False and result["exit_code"] == 0
    assert result["problems"] == ["src/lib.rs::tests::adds: tests::adds is listed but has no result of its own"]


# ---- every id a failure line gives is one check takes back ------------------------------------------


def failure_ids(result):
    return [f[len("FAILED ") :].split(": ", 1)[0] for f in result["failures"] if not f.startswith("FAILED doctest")]


@needs_rust_parser
@pytest.mark.parametrize("name", ["rawmod_all", "rawmod_file"])
def test_a_failure_in_a_raw_identifier_module_or_test_is_named_by_the_file_it_is_in(name):
    ids = failure_ids(verdict(name))
    assert "src/type.rs::tests::t_fails" in ids and "src/type.rs::tests::r#match" in ids
    if name == "rawmod_all":
        assert "src/lib.rs::r#in::tests::r#fn" in ids
    assert not any("::r#type::" in i for i in ids), ids


@needs_rust_parser
@pytest.mark.parametrize("name", ["rawmod_all", "rawmod_file", "fail", "macgen_file", "attrgen_file", "decorated"])
def test_every_failure_id_goes_through_normalize_and_is_taken_back_by_the_request_it_stands_for(name):
    repo = repo_of(name)
    ids = failure_ids(verdict(name))
    assert ids, name
    for ident in ids:
        taken = normalize(repo, [ident])
        request = parse_wants(repo, taken, Model(repo))  # raises ValueError for an id it refuses
        assert len(request.picks) >= 1 and all(p.names and len(p.names) == 1 for p in request.picks), (ident, request)


@needs_rust_parser
def test_the_module_a_raw_identifier_failure_is_found_in_is_compared_by_its_bare_name():
    repo = repo_of("rawmod_all")
    model = Model(repo)
    assert model.locate("src/lib.rs", "r#type::tests::t_fails") == ("src/type.rs", "tests::t_fails")
    assert model.locate("src/lib.rs", "type::tests::t_fails") == ("src/type.rs", "tests::t_fails")
    assert model.locate("src/lib.rs", "r#in::tests::r#fn") == ("src/lib.rs", "r#in::tests::r#fn")
    assert model.locate("src/lib.rs", "r#type::tests::r#match") == ("src/type.rs", "tests::r#match")


# ---- the forgery limit: caught where the run is narrower than the binary's list --------------------


@needs_rust_parser
def test_a_file_that_is_the_whole_test_target_has_no_list_so_the_forgery_in_it_reads_as_passed():
    # `check tests/it.rs` is `--test it` whole: no `--list`, no filter, nothing to compare the forged result line with
    assert meta("forge_it_file")["exit_code"] == 0 and meta("forge_it_file")["listed"] is False
    assert meta("forge_it_file")["argv"][-2:] == ["--test", "it"]
    result = verdict("forge_it_file")
    assert result["passed"] is True and result["counts"]["passed"] == 2 and result["problems"] == []


@needs_rust_parser
def test_only_a_run_narrower_than_the_binarys_list_has_a_list_to_catch_a_forgery_with():
    assert run_of("forge_it_file").invocation.listing is False  # the whole target
    assert run_of("forge_all").invocation.listing is False  # no arguments
    assert run_of("forge_a").invocation.listing is True  # an id
    assert run_of("macgen_file").invocation.listing is True  # a file below a crate root
    assert run_of("macgen_dir").invocation.listing is True  # a directory below a crate root
    assert run_of("pass_both").invocation.listing is False  # a crate root file
    assert run_of("macgen_src_dir").invocation.listing is False  # a directory that holds the crate root
