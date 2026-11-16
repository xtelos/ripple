"""`check` for Rust against the real toolchain, end to end: each fixture crate is built, run and judged for real.

Skipped, with the reason, when `cargo` is not on PATH. Offline (CARGO_NET_OFFLINE), the crates need nothing from a registry,
and each test builds into its own target directory so nothing is written into the repository.
"""

import shutil

import pytest
from rs_check_support import CRATES, needs_rust_parser

from ripple import queries
from ripple.cache import load_repo
from ripple.checker import run_check
from ripple.profile import RUST

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo is not on PATH")


@pytest.fixture(autouse=True)
def offline_cargo(tmp_path, monkeypatch):
    monkeypatch.setenv("CARGO_NET_OFFLINE", "true")
    monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path / "target-dir"))
    for key in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS", "CARGO_BUILD_TARGET", "RUST_TEST_NOCAPTURE", "RUST_TEST_THREADS", "RUSTC_WRAPPER"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def crate(tmp_path):
    """Copy a fixture crate out of the repo, so cargo runs in a directory ripple's own tree does not contain."""

    def copy(name):
        target = tmp_path / name
        shutil.copytree(CRATES / name, target)
        return target

    return copy


def check(repo, tests=None, timeout=240):
    return run_check(repo, tests=tests, timeout=timeout, profile=RUST)


def said(result, *words):
    return any(all(w in p for w in words) for p in result["problems"])


def test_a_passing_crate_with_no_arguments_passes_and_runs_the_workspace(crate):
    result = check(crate("pass"))
    assert result["passed"] is True, result
    assert result["counts"]["passed"] == 3  # two unit tests and the doc test
    assert result["command"].endswith("--no-fail-fast --workspace")


@needs_rust_parser
def test_a_failing_test_fails_and_is_named_so_it_can_be_passed_back(crate):
    repo = crate("fail")
    result = check(repo)
    assert result["passed"] is False
    assert result["failures"] == ["FAILED src/lib.rs::tests::bad: panicked at src/lib.rs:14:9: assertion `left == right` failed"]
    # the id ripple names is one it accepts: the failing test alone fails, the passing one alone passes
    assert check(repo, ["src/lib.rs::tests::bad"])["passed"] is False
    assert check(repo, ["src/lib.rs::tests::adds"])["passed"] is True


@pytest.mark.parametrize(
    "name, words",
    [
        ("build_failure", ("build failed", "cannot find value `missing`")),
        ("test_build_failure", ("build failed", "mismatched types")),
        ("build_script_failure", ("build failed", "custom build command")),
        ("missing_dependency", ("build failed", "ripple_no_such_crate_xyz")),
    ],
)
def test_a_build_failure_is_not_a_pass(crate, name, words):
    result = check(crate(name))
    assert result["passed"] is False and said(result, *words), result


def test_a_crate_with_no_tests_is_not_a_pass(crate):
    result = check(crate("no_tests"))
    assert result["passed"] is False and said(result, "no test ran")


@needs_rust_parser
def test_a_test_behind_a_feature_that_is_off_is_not_a_pass_when_asked_for_by_name(crate):
    result = check(crate("gated"), ["src/lib:tests::slow"])
    assert result["passed"] is False and result["exit_code"] == 0
    assert said(result, "no test named tests::slow exists in gated")


@needs_rust_parser
def test_an_ignored_test_asked_for_is_not_a_pass_and_in_a_whole_run_it_is_counted(crate):
    repo = crate("ignored")
    assert check(repo, ["src/lib:tests::slow"])["passed"] is False
    whole = check(repo)
    assert whole["passed"] is True and whole["counts"]["ignored"] == 2


def test_a_doctest_failure_is_not_a_pass(crate):
    result = check(crate("doctest_failure"))
    assert result["passed"] is False and result["failures"][0].startswith("FAILED doctest src/lib.rs - add")


@pytest.mark.parametrize("name, words", [("abort", ("the test binary died", "SIGABRT")), ("exit_early", ("exited without reporting a result",)), ("killed", ("the test binary died", "SIGKILL"))])
def test_a_binary_that_aborts_exits_early_or_is_killed_is_not_a_pass(crate, name, words):
    result = check(crate(name))
    assert result["passed"] is False and said(result, *words), result


def test_a_target_with_its_own_harness_is_judged_on_its_exit_status(crate):
    assert check(crate("own_harness_ok"))["passed"] is True
    result = check(crate("own_harness_panic"))
    assert result["passed"] is False and said(result, "its own test harness failed")


def test_ripples_timeout_kills_the_run_and_is_not_a_pass(crate):
    result = check(crate("sleepy"), timeout=8)
    assert result["passed"] is False and result["timed_out"] is True
    assert said(result, "timed out after 8s")


@needs_rust_parser
def test_a_name_that_is_a_prefix_of_another_runs_only_the_one_asked_for(crate):
    repo = crate("pass")
    result = check(repo, ["src/lib:tests::adds"])
    assert result["passed"] is True and result["counts"]["passed"] == 1
    both = check(repo, ["src/lib:tests::adds", "src/lib:tests::adds_zero"])
    assert both["counts"]["passed"] == 2 and both["passed"] is True


@needs_rust_parser
def test_the_same_test_name_in_two_packages_runs_only_the_package_asked_for(crate):
    repo = crate("dupes")
    a = check(repo, ["a/src/lib:tests::it_works"])
    assert a["passed"] is True and a["counts"] == {**a["counts"], "passed": 1, "failed": 0}
    b = check(repo, ["b/src/lib:tests::it_works"])
    assert b["passed"] is False and b["counts"]["failed"] == 1 and b["counts"]["passed"] == 0
    whole = check(repo)
    assert whole["passed"] is False and whole["counts"]["passed"] == 2 and whole["counts"]["failed"] == 1


@needs_rust_parser
def test_the_same_name_in_the_lib_the_bin_and_an_integration_test_runs_one_of_them(crate):
    repo = crate("mixed")
    for given in ("src/lib:tests::same", "src/main:tests::same", "tests/it:same"):
        result = check(repo, [given])
        assert result["passed"] is True and result["counts"]["passed"] == 1, (given, result)


@needs_rust_parser
def test_files_and_directories_run_exactly_the_tests_in_them(crate):
    repo = crate("mixed")
    assert check(repo, ["src/util.rs"])["counts"]["passed"] == 1
    assert check(repo, ["src/net"])["counts"]["passed"] == 1
    assert check(repo, ["tests/it.rs"])["counts"]["passed"] == 3  # the whole test target, with the shared module's test
    assert check(repo, ["tests"])["counts"]["passed"] == 5
    common = check(repo, ["tests/common/mod.rs"])  # one test, in two targets: two runs
    assert common["passed"] is True and common["counts"]["passed"] == 2
    assert check(repo, ["."])["counts"]["passed"] == 11 and check(repo)["counts"]["passed"] == 11


@needs_rust_parser
def test_a_hyphenated_package_and_bin_are_selected_by_their_real_names(crate):
    result = check(crate("hyphen"), ["src/bin/my-tool:tests::t_tool"])
    assert result["passed"] is True and result["counts"]["passed"] == 1


@needs_rust_parser
def test_the_ids_impact_emits_run_the_tests_they_name(crate, monkeypatch, tmp_path_factory):
    monkeypatch.setenv("RIPPLE_CACHE_DIR", str(tmp_path_factory.mktemp("cache")))
    repo = crate("mixed")
    graph = load_repo(repo).graph
    callers = [e["symbol"] for es in queries.impact(graph, "add")["by_file"].values() for e in es if e["symbol"] != "src/main:main"]
    result = check(repo, callers)
    assert result["passed"] is True and result["counts"]["passed"] == 5, result
    assert len(callers) == 5


def test_a_test_target_that_holds_no_test_is_not_a_pass(crate):
    result = check(crate("empty_target"))
    assert result["passed"] is False and said(result, "the test target ran no test")


@needs_rust_parser
def test_a_target_whose_features_are_off_is_named_in_a_whole_run_and_refused_when_asked_for(crate):
    repo = crate("needs_feature")
    result = check(repo)
    assert result["passed"] is True and any("needs the features extra" in n for n in result["notes"])
    with pytest.raises(ValueError, match="requires the features extra"):
        check(repo, ["tests/needs.rs"])


def test_quiet_verbose_and_colour_in_the_environment_do_not_change_the_verdict(crate, monkeypatch):
    monkeypatch.setenv("CARGO_TERM_QUIET", "true")
    monkeypatch.setenv("CARGO_TERM_VERBOSE", "true")
    monkeypatch.setenv("CARGO_TERM_COLOR", "always")
    result = check(crate("pass"))
    assert result["passed"] is True and result["counts"]["passed"] == 3


def test_libtests_nocapture_is_never_read_as_a_pass_and_is_named(crate, monkeypatch):
    monkeypatch.setenv("RUST_TEST_NOCAPTURE", "1")
    result = check(crate("printer"))
    assert result["passed"] is False and any("RUST_TEST_NOCAPTURE" in n for n in result["notes"])


def test_a_workspace_inside_another_is_run_only_when_asked_and_a_stray_manifest_is_named(crate):
    repo = crate("nested")
    whole = check(repo)
    assert whole["passed"] is True and whole["counts"]["passed"] == 1  # app only
    assert any("inside another workspace but not part of it" in n and "fixture" in n for n in whole["notes"])
    assert any("stray" in n and "cargo cannot read" in n for n in whole["notes"])
    inside = check(repo, ["fixture"])
    assert inside["passed"] is True and inside["counts"]["passed"] == 1
    assert "cd fixture &&" in inside["command"]
    with pytest.raises(ValueError, match="cargo cannot read stray/Cargo.toml"):
        check(repo, ["stray"])


def test_a_toolchain_file_of_the_repo_is_named(crate):
    repo = crate("pass")
    (repo / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "stable"\n')
    result = check(repo)
    assert any("rust-toolchain.toml selects the toolchain" in n for n in result["notes"])


# ---- what a file or directory covers is what the test binary lists -------------------------------------


@pytest.mark.parametrize("given", ["src/util.rs", "src", "src/util.rs::tests::generated_fails", "src/util:tests::generated_fails"])
@needs_rust_parser
def test_a_failing_test_a_macro_writes_fails_a_file_a_directory_or_an_id(crate, given):
    # the test is written by a macro_rules macro defined in another file: no reading of util.rs finds it, the binary lists it
    result = check(crate("macgen"), [given])
    assert result["passed"] is False, result
    assert any(f.startswith("FAILED src/util.rs::tests::generated_fails") for f in result["failures"]), result["failures"]


@needs_rust_parser
def test_a_directory_below_the_crate_root_runs_every_listed_test_of_its_modules(crate):
    result = check(crate("macgen"), ["src/sub"])
    assert result["passed"] is False and result["counts"]["passed"] == 1 and result["counts"]["failed"] == 1
    assert "--exact sub::tests::sub_generated_fails sub::tests::sub_plain" in result["command"]


@needs_rust_parser
def test_the_tests_an_attribute_macro_adds_are_run_for_the_file_they_are_in(crate):
    result = check(crate("attrgen"), ["attrgen/src/util.rs"])
    assert result["passed"] is False and result["counts"]["passed"] == 3 and result["counts"]["failed"] == 2
    assert {f.split(": ")[0] for f in result["failures"]} == {
        "FAILED attrgen/src/util.rs::tests::broken_a", "FAILED attrgen/src/util.rs::tests::broken_b"
    }  # fmt: skip


@needs_rust_parser
def test_an_id_that_is_in_no_list_is_no_test_of_that_name_and_nothing_is_run(crate):
    result = check(crate("macgen"), ["src/util.rs::tests::not_a_test"])
    assert result["passed"] is False and said(result, "no test named util::tests::not_a_test exists in macgen")
    assert result["counts"]["binaries"] == 0


@needs_rust_parser
def test_a_file_that_is_declared_with_a_path_attribute_runs_the_whole_target_and_says_so(crate):
    result = check(crate("pathmod"), ["src/elsewhere/x.rs"])
    assert result["passed"] is True and result["counts"]["passed"] == 1
    assert any("not certain (a module on the way is declared with #[path])" in n for n in result["notes"])


@needs_rust_parser
def test_a_crate_root_file_is_the_whole_target(crate):
    result = check(crate("decorated"), ["src/lib.rs"])
    assert result["passed"] is False and result["counts"]["passed"] == 4 and result["counts"]["failed"] == 2
    assert any("is the root of the lib target, so every test of it ran" in n for n in result["notes"])


def test_the_list_is_one_build_not_two(crate, monkeypatch):
    # `cargo test ... -- --list` builds the binary and the run after it finds it built: one Compiling line in the pair
    import subprocess

    repo = crate("macgen")
    argv = ["cargo", "test", "--color", "never", "-p", "macgen", "--lib"]
    listed = subprocess.run(argv + ["--", "--list"], cwd=repo, capture_output=True, text=True)
    ran = subprocess.run(argv + ["--", "--exact", "util::tests::plain"], cwd=repo, capture_output=True, text=True)
    assert "Compiling macgen" in listed.stderr and "Compiling" not in ran.stderr


# ---- libtest's decorations ---------------------------------------------------------------------------------


@pytest.mark.parametrize("given", ["src/lib:tests::sp", "src/lib.rs::tests::sp_msg", "src/lib.rs::tests::r#match", "src/lib:tests::plain"])
@needs_rust_parser
def test_a_should_panic_test_and_a_raw_identifier_test_asked_for_by_id_pass(crate, given):
    result = check(crate("decorated"), [given])
    assert result["passed"] is True and result["counts"]["passed"] == 1, result


@needs_rust_parser
def test_a_should_panic_test_that_did_not_panic_fails_under_its_name(crate):
    result = check(crate("decorated"), ["src/lib:tests::sp_no_panic"])
    assert result["passed"] is False and result["failures"][0].startswith("FAILED src/lib.rs::tests::sp_no_panic: note: test did not panic as expected")


def test_a_whole_run_reads_every_decoration_of_the_unit_tests_and_the_doc_tests(crate):
    result = check(crate("decorated"))
    assert result["counts"] == {**result["counts"], "passed": 8, "failed": 3, "ignored": 3}
    assert result["problems"] == []


# ---- a helper file, an example, a binary that exits before libtest, a forged result --------------------


@needs_rust_parser
def test_a_helper_file_of_tests_that_another_target_reads_is_a_note_and_the_run_passes(crate):
    result = check(crate("helper"))
    assert result["passed"] is True and any("helper/util (tests/util.rs) holds no test" in n for n in result["notes"])


@needs_rust_parser
def test_an_example_with_a_test_runs_by_file_and_by_id(crate):
    repo = crate("examples_ok")
    for given in ("examples/demo.rs", "examples/demo:tests::ex_ok", "examples"):
        result = check(repo, [given])
        assert result["passed"] is True and result["counts"]["passed"] == 1, (given, result)
        assert "--example demo" in result["command"]


def test_a_binary_that_exits_zero_before_libtest_starts_is_not_a_pass(crate):
    result = check(crate("pre"))
    assert result["exit_code"] == 0 and result["passed"] is False
    assert said(result, "pre/early", "the test binary printed no libtest output and no result")


@needs_rust_parser
def test_a_binary_that_exits_before_libtest_starts_has_no_list_and_nothing_is_run_for_an_id_in_it(crate):
    result = check(crate("pre"), ["tests/early:would_fail"])
    assert result["passed"] is False and said(result, "printed no list of its tests that adds up")


@needs_rust_parser
def test_a_forged_result_is_caught_for_an_id_and_still_read_as_passed_in_a_whole_run(crate):
    repo = crate("forge")
    assert check(repo, ["tests/it:a"])["passed"] is False  # the list says 2 tests, the forged line says 0 filtered out
    whole = check(repo)  # what remains: no list to compare with
    assert whole["passed"] is True and whole["counts"]["passed"] == 2


def test_a_target_directory_with_parentheses_in_its_path_is_read(crate, tmp_path, monkeypatch):
    monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path / "target (copy)"))
    result = check(crate("pass"))
    assert result["passed"] is True and result["counts"]["passed"] == 3
