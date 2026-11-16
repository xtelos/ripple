"""What `check` asks `cargo test` to run for Rust: the argv for each request, and the arguments it refuses (no cargo needed).

A fake `cargo` on PATH prints a captured run (and a captured `cargo metadata`) and logs how it was called, so the
whole of `run_check` is exercised: the refusals happen before it is ever called, and the verdict is the captured one.
"""

import json
import os
import stat
import sys
from collections import defaultdict

import pytest
from rs_check_support import CAPTURES, CRATES, invocation, list_text, meta, names, needs_rust_parser, repo_of, restore, space

from ripple import queries
from ripple.cache import load_repo
from ripple.checker import RUNNERS, run_check, runner_for
from ripple.profile import GO, PYTHON, RUST
from ripple.rs_check import BASE_ARGV, Invocation, Model, Pick, Request, _uncertain, normalize, parse_wants, plan, resolve

FAKE_CARGO = """#!{python}
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_CARGO_LOG"]
before = [json.loads(line) for line in open(log)] if os.path.exists(log) else []
with open(log, "a") as handle:
    handle.write(json.dumps({{"argv": args, "cwd": os.getcwd()}}) + "\\n")
if args[0] == "metadata":
    sys.stdout.write(open(os.environ["FAKE_CARGO_META"]).read())
    sys.exit(0)
outs = json.loads(os.environ["FAKE_CARGO_OUTS"])
path, code = outs[min(sum(1 for b in before if b["argv"][0] == "test"), len(outs) - 1)]
sys.stdout.write(open(path).read())
sys.exit(code)
"""


@pytest.fixture
def fake_cargo(tmp_path_factory, monkeypatch):
    """A `cargo` on PATH that logs its calls and prints captured output; `use(*captures)` picks what its n-th test run prints."""
    bin_dir = tmp_path_factory.mktemp("bin")
    script = bin_dir / "cargo"
    script.write_text(FAKE_CARGO.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = bin_dir / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CARGO_LOG", str(log))

    def use(*captures: str):
        outs = []
        for capture in captures:
            if meta(capture)["listed"]:  # a request that is not a whole target is first asked for the binary's list
                listing = bin_dir / f"{capture}.list.out"
                listing.write_text(list_text(capture))
                outs.append([str(listing), 0])
            path = bin_dir / f"{capture}.out"
            path.write_text(restore((CAPTURES / f"{capture}.txt").read_text(), capture))
            outs.append([str(path), meta(capture)["exit_code"] if meta(capture)["exit_code"] is not None else 1])
        metadata = bin_dir / "metadata.json"
        metadata.write_text(restore(meta(captures[0])["metadata"], captures[0]))
        monkeypatch.setenv("FAKE_CARGO_META", str(metadata))
        monkeypatch.setenv("FAKE_CARGO_OUTS", json.dumps(outs))
        return log

    return use


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def cargo_test_calls(log):
    """The `cargo test` runs that were made to run tests (not the ones that only list them)."""
    return [c for c in calls(log) if c["argv"][0] == "test" and c["argv"][-1] != "--list"]


def list_calls(log):
    return [c for c in calls(log) if c["argv"][0] == "test" and c["argv"][-1] == "--list"]


def wants(repo, tests):
    return parse_wants(repo, normalize(repo, tests))


def planned(repo, tests, name):
    return plan([space(name)], wants(repo, tests), repo)


# ---- every capture's command is the one ripple builds for its request ---------------------------


@needs_rust_parser
def test_ripple_builds_the_commands_each_request_recorded():
    recorded = [n for n in names() if meta(n)["request"] and not meta(n)["by_hand"]]
    assert len(recorded) >= 30
    for name in recorded:
        inv = invocation(name)  # a request that needs the list is resolved with the list its capture recorded
        assert inv.argv() == meta(name)["argv"], name
        assert inv.listing is meta(name)["listed"], name


@needs_rust_parser
def test_a_request_that_is_not_a_whole_target_lists_first_with_the_same_selection_and_nothing_else():
    for name in (n for n in names() if meta(n)["listed"]):
        inv = invocation(name)
        assert inv.list_argv() == meta(name)["argv"][: meta(name)["argv"].index("--")] + ["--", "--list"], name


def test_no_arguments_is_the_whole_workspace_in_each_workspace():
    for name in ("pass", "dupes_all", "mixed_all", "nested_all"):
        assert [i.argv() for i in plan([space(name)], Request(), repo_of(name))] == [meta(name)["argv"]]


def test_the_argv_always_has_the_options_that_keep_the_output_readable():
    assert BASE_ARGV[:2] == ["cargo", "test"]
    for option in ("--no-fail-fast", "--message-format"):
        assert option in BASE_ARGV
    assert BASE_ARGV[BASE_ARGV.index("--color") + 1] == "never"
    assert "term.quiet=false" in BASE_ARGV and "term.verbose=false" in BASE_ARGV  # outrank CARGO_TERM_QUIET and CARGO_TERM_VERBOSE


@needs_rust_parser
def test_a_name_is_filtered_exactly_never_as_a_substring():
    # without --exact, `tests::adds` would also run `tests::adds_zero`
    repo = repo_of("pass")
    [run] = planned(repo, ["src/lib:tests::adds"], "pass")
    assert run.argv()[-3:] == ["--", "--exact", "tests::adds"]


@needs_rust_parser
def test_two_names_of_one_target_are_one_run_filtered_to_exactly_those_two():
    repo = repo_of("pass")
    [run] = planned(repo, ["src/lib:tests::adds", "src/lib:tests::adds_zero", "src/lib:tests::adds"], "pass")
    assert run.argv()[-4:] == ["--", "--exact", "tests::adds", "tests::adds_zero"]
    assert run.names == ("tests::adds", "tests::adds_zero")


@needs_rust_parser
def test_names_of_different_targets_are_never_one_run_so_a_filter_cannot_reach_a_test_nobody_asked_for():
    # the lib, the bin and the integration test each have a test named `same`: one filter would run all three
    repo = repo_of("mixed_all")
    runs = planned(repo, ["src/lib:tests::same", "src/main:tests::same", "tests/it:same"], "mixed_all")
    assert sorted(r.selectors for r in runs) == [
        ["-p", "mixed", "--bin", "mixed"],
        ["-p", "mixed", "--lib"],
        ["-p", "mixed", "--test", "it"],
    ]
    assert sorted(r.names for r in runs) == [("same",), ("tests::same",), ("tests::same",)]


@needs_rust_parser
def test_names_of_different_packages_with_the_same_test_are_two_runs_each_in_its_own_package():
    repo = repo_of("dupes_all")
    runs = planned(repo, ["a/src/lib:tests::it_works", "b/src/lib:tests::it_works"], "dupes_all")
    assert sorted(r.selectors for r in runs) == [["-p", "a", "--lib"], ["-p", "b", "--lib"]]
    assert all(r.names == ("tests::it_works",) for r in runs)


@needs_rust_parser
def test_a_file_two_test_targets_include_is_one_run_per_target_with_its_module_path_as_the_prefix():
    repo = repo_of("mixed_all")
    runs = planned(repo, ["tests/common/mod.rs"], "mixed_all")
    assert sorted(r.selectors for r in runs) == [["-p", "mixed", "--test", "it"], ["-p", "mixed", "--test", "other"]]
    assert all(r.pending and r.listing and r.picks[0].prefixes == (("common",),) for r in runs)


@needs_rust_parser
def test_a_file_is_its_module_path_and_the_list_decides_what_is_in_it_never_a_reading_of_the_file():
    repo = repo_of("mixed_all")
    [run] = planned(repo, ["src/util.rs"], "mixed_all")
    assert run.pending and run.names == () and run.picks[0].prefixes == (("util",),)
    with pytest.raises(RuntimeError):
        run.argv()  # no command exists until the binary has listed its tests
    assert run.list_argv()[len(BASE_ARGV):] == ["-p", "mixed", "--lib", "--", "--list"]
    assert invocation("mixed_util_file").argv()[len(BASE_ARGV):] == ["-p", "mixed", "--lib", "--", "--exact", "util::tests::t_util"]
    [run] = planned(repo, ["tests/it.rs"], "mixed_all")
    assert not run.listing and run.argv()[len(BASE_ARGV):] == ["-p", "mixed", "--test", "it"]  # the root of an integration test is its target


@needs_rust_parser
def test_a_crate_root_file_is_the_whole_target_and_the_verdict_says_so():
    repo = repo_of("mixed_all")
    request = wants(repo, ["src/lib.rs"])
    assert [p.whole for p in request.picks] == [True]
    assert any("is the root of the lib target, so every test of it ran" in n for n in request.notes)


@needs_rust_parser
def test_a_directory_is_the_modules_below_it_and_a_package_directory_is_the_whole_package():
    repo = repo_of("mixed_all")
    [run] = planned(repo, ["src/net"], "mixed_all")
    assert run.picks[0].prefixes == (("net",),) and run.pending
    assert [r.argv()[len(BASE_ARGV):] for r in planned(repo, ["."], "mixed_all")] == [["--workspace"]]
    dupes = repo_of("dupes_all")
    assert [r.argv()[len(BASE_ARGV):] for r in planned(dupes, ["a"], "dupes_all")] == [["-p", "a"]]
    assert [r.argv()[len(BASE_ARGV):] for r in planned(dupes, ["a", "b"], "dupes_all")] == [["--workspace"]]


@needs_rust_parser
def test_a_directory_with_a_crate_root_in_it_is_that_whole_target():
    repo = repo_of("mixed_all")
    runs = planned(repo, ["src"], "mixed_all")  # src/lib.rs and src/main.rs are crate roots
    assert sorted(r.selectors for r in runs) == [["-p", "mixed", "--bin", "mixed"], ["-p", "mixed", "--lib"]]
    assert all(not r.listing for r in runs)


@needs_rust_parser
def test_a_directory_of_test_files_runs_each_whole():
    repo = repo_of("mixed_all")
    runs = planned(repo, ["tests"], "mixed_all")
    assert sorted(r.selectors for r in runs) == [["-p", "mixed", "--test", "it"], ["-p", "mixed", "--test", "other"]]
    assert all(not r.listing for r in runs)


@needs_rust_parser
def test_a_hyphenated_target_is_selected_by_its_real_name():
    repo = repo_of("hyphen_bin")
    [run] = planned(repo, ["src/bin/my-tool:tests::t_tool"], "hyphen_bin")
    assert run.selectors == ["-p", "hy-phen", "--bin", "my-tool"]


# ---- the ids impact emits are ids check accepts ----------------------------------------------------


@needs_rust_parser
def test_impact_lists_a_rust_test_as_a_caller_with_a_file_id_and_no_test_ids():
    graph = load_repo(CRATES / "mixed").graph
    result = queries.impact(graph, "add")
    assert result["tests"] == []
    assert result["by_file"]["src/lib.rs"][0]["symbol"] == "src/lib:tests::same"
    assert "tests/it:t_it" in [e["symbol"] for e in result["by_file"]["tests/it.rs"]]


@needs_rust_parser
def test_the_ids_impact_emits_for_tests_run_the_test_they_name():
    repo = repo_of("mixed_all")
    graph = load_repo(CRATES / "mixed").graph
    callers = [e["symbol"] for es in queries.impact(graph, "double")["by_file"].values() for e in es]
    assert callers == ["src/util:tests::t_util"]
    asked = normalize(repo, callers)
    assert asked == ["src/util.rs::tests::t_util"]
    [run] = plan([space("mixed_all")], parse_wants(repo, asked), repo)
    assert run.names == ("util::tests::t_util",) and run.selectors == ["-p", "mixed", "--lib"]


@pytest.mark.parametrize(
    "given, asked",
    [
        ("src/lib:tests::same", "src/lib.rs::tests::same"),
        ("src/net:tests::t_net", "src/net/mod.rs::tests::t_net"),
        ("tests/it:same", "tests/it.rs::same"),
        ("tests/common:t_common", "tests/common/mod.rs::t_common"),
        ("src/lib.rs::tests::same", "src/lib.rs::tests::same"),
        ("src/lib.rs", "src/lib.rs"),
        ("src/net", "src/net"),
    ],
)
def test_a_symbol_id_is_the_file_and_name_it_names(given, asked):
    assert normalize(repo_of("mixed_all"), [given]) == [asked]


def test_an_id_that_names_no_file_is_left_for_check_to_refuse():
    assert normalize(repo_of("mixed_all"), ["src/nowhere:tests::x"]) == ["src/nowhere:tests::x"]


def test_normalize_leaves_what_is_not_a_string_for_check_tests_to_refuse():
    assert normalize(repo_of("mixed_all"), [None, 3, b"x"]) == [None, 3, b"x"]  # type: ignore[list-item]


@pytest.mark.parametrize("bad", ["src//lib:tests::x", "../src/lib:x", "src/../lib:x", "/etc/passwd:x", "./src/lib:x", "src/lib\x00:x"])
def test_normalize_rejects_a_malformed_id_outright_before_any_file_is_looked_at(bad, monkeypatch):
    from pathlib import Path

    looked = []
    for method in ("is_file", "exists", "is_dir"):
        real = getattr(Path, method)
        monkeypatch.setattr(Path, method, lambda self, *a, _real=real, **k: looked.append(self) or _real(self, *a, **k))
    repo = repo_of("mixed_all")
    with pytest.raises(ValueError):
        normalize(repo, [bad])
    assert looked == []


def test_an_id_with_whitespace_is_left_alone_unread_and_refused_by_check_tests(fake_cargo):
    log = fake_cargo("mixed_all")
    repo = repo_of("mixed_all")
    assert normalize(repo, ["a\nb:x"]) == ["a\nb:x"]
    with pytest.raises(ValueError):
        run_check(repo, tests=["a\nb:x"], profile=RUST)
    assert calls(log) == []


def test_normalize_does_not_follow_a_link_out_of_the_repo(tmp_path, make_repo):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "lib.rs").write_text("#[test]\nfn t() {}\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.toml").write_text('[package]\nname = "p"\nversion = "0.1.0"\n')
    (repo / "link").symlink_to(outside)
    (repo / "evil.rs").symlink_to(outside / "lib.rs")
    assert normalize(repo, ["link/lib:t", "evil:t"]) == ["link/lib:t", "evil:t"]


# ---- every argument that is not a test path is refused before cargo is called ------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "--features=x", "-p", "--manifest-path=/etc/x", "@/etc/passwd", "", "../outside.rs", "/etc", "/etc/passwd",
        "src/lib.rs::", "src/lib.rs::tests::", "src/lib.rs::nomod::t", "src/lib.rs::not a name", "src/lib.rs::1x",
        "src/missing.rs::t", "src/net::tests::t_net", "Cargo.toml", "Cargo.lock", "src/lib:nomod::t", "src/nowhere:tests::x",
        "src/main:main", "src/lib:add", "docs",
    ],
)
def test_an_argument_that_is_an_option_a_file_arg_or_outside_the_repo_or_no_test_is_refused_before_cargo_runs(fake_cargo, bad):
    log = fake_cargo("mixed_all")
    with pytest.raises(ValueError):
        run_check(repo_of("mixed_all"), tests=[bad], profile=RUST)
    assert calls(log) == []


def test_one_bad_argument_among_good_ones_refuses_the_lot_before_cargo_runs(fake_cargo):
    log = fake_cargo("mixed_all")
    with pytest.raises(ValueError):
        run_check(repo_of("mixed_all"), tests=["src/lib:tests::same", "--release"], profile=RUST)
    assert calls(log) == []


def test_a_path_that_leaves_the_repo_by_a_link_is_refused(fake_cargo, tmp_path):
    log = fake_cargo("mixed_all")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.toml").write_text('[package]\nname = "p"\nversion = "0.1.0"\n')
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x.rs").write_text("#[test]\nfn t() {}\n")
    (repo / "x.rs").symlink_to(tmp_path / "elsewhere" / "x.rs")
    for bad in ("x.rs", "x.rs::t", "x:t"):
        with pytest.raises(ValueError):
            run_check(repo, tests=[bad], profile=RUST)
    assert calls(log) == []


@needs_rust_parser
def test_a_function_that_is_not_a_test_is_refused_and_a_module_the_file_lacks_is_named():
    repo = repo_of("mixed_all")
    with pytest.raises(ValueError, match="no attribute, so it is not a test"):
        parse_wants(repo, ["src/main.rs::main"])
    with pytest.raises(ValueError, match="has no module 'nomod' .*inline modules: tests"):
        parse_wants(repo, ["src/lib.rs::nomod::t"])
    with pytest.raises(ValueError, match="is not a Rust source file"):
        parse_wants(repo, ["Cargo.toml"])


@needs_rust_parser
def test_a_name_the_file_does_not_declare_is_not_refused_because_a_macro_may_write_it_the_list_says(make_repo):
    # a test a macro generates is no function in the file: whether it exists is the test binary's to say
    repo = make_repo({"Cargo.toml": '[package]\nname = "p"\nversion = "0.1.0"\n', "src/lib.rs": "#[cfg(test)]\nmod tests {\n    case!(generated);\n}\n"})
    [pick] = parse_wants(repo, ["src/lib.rs::tests::generated"]).picks
    assert pick.names == ("tests::generated",)


@needs_rust_parser
def test_a_file_with_no_test_in_it_is_a_module_prefix_not_a_refusal_the_list_decides(make_repo):
    repo = make_repo({"Cargo.toml": '[package]\nname = "p"\nversion = "0.1.0"\n', "src/lib.rs": "pub mod util;\n", "src/util.rs": "pub fn f() {}\n"})
    [pick] = parse_wants(repo, ["src/util.rs"]).picks
    assert pick.prefixes == (("util",),)


@needs_rust_parser
def test_a_file_no_crate_includes_is_refused(make_repo):
    repo = make_repo({"Cargo.toml": '[package]\nname = "p"\nversion = "0.1.0"\n', "src/lib.rs": "", "src/orphan.rs": "#[test]\nfn t() {}\n"})
    with pytest.raises(ValueError, match="no crate of the package p includes src/orphan.rs"):
        parse_wants(repo, ["src/orphan.rs"])


@pytest.mark.parametrize(
    "given, why",
    [
        ("benches/b.rs", "bench target"),
        ("build.rs::t", "build target"),
    ],
)
def test_a_bench_or_a_build_script_is_not_run(make_repo, given, why):
    repo = make_repo(
        {
            "Cargo.toml": '[package]\nname = "p"\nversion = "0.1.0"\n',
            "src/lib.rs": "",
            "benches/b.rs": "#[test]\nfn t() {}\n",
            "build.rs": "fn main() {}\n#[test]\nfn t() {}\n",
        }
    )
    with pytest.raises(ValueError):
        parse_wants(repo, [given])


def test_a_whole_run_names_the_targets_cargo_leaves_out_for_features_that_are_off():
    from ripple.rs_check import skipped_targets

    spaces = [space("needs_feature_all")]
    runs = plan(spaces, Request(), repo_of("needs_feature_all"))
    assert skipped_targets(spaces, runs) == ["needs_feature/needs (tests/needs.rs) needs the features extra, which are off, so cargo skipped it"]
    assert skipped_targets([space("mixed_all")], plan([space("mixed_all")], Request(), repo_of("mixed_all"))) == []


@needs_rust_parser
def test_a_target_cargo_would_skip_or_cannot_filter_is_refused_by_the_plan(make_repo):
    repo = repo_of("needs_feature_all")
    with pytest.raises(ValueError, match="requires the features extra, which are off"):
        planned(repo, ["tests/needs.rs"], "needs_feature_all")


@needs_rust_parser
def test_a_name_filter_for_a_target_with_its_own_harness_is_refused_by_the_plan(tmp_path):
    import shutil

    from ripple.rs_check import parse_metadata

    repo = tmp_path / "own"
    shutil.copytree(repo_of("own_harness_ok"), repo)
    (repo / "tests" / "it.rs").write_text("#[test]\nfn t() {}\nfn main() {}\n")
    metadata = restore(meta("own_harness_ok")["metadata"], "own_harness_ok").replace(str(repo_of("own_harness_ok")), str(repo))
    with pytest.raises(ValueError, match="`harness = false`"):
        plan([parse_metadata(metadata, repo)], parse_wants(repo, ["tests/it.rs::t"]), repo)


# ---- a directory that holds only another workspace's packages --------------------------------------


@needs_rust_parser
def test_a_nested_workspace_is_run_only_when_a_path_asks_for_it_and_never_by_default():
    root, fixture = space("nested_all"), space("nested_fixture")
    fixture.nested = True
    repo = repo_of("nested_all")
    assert [i.argv()[len(BASE_ARGV):] for i in plan([root, fixture], Request(), repo)] == [["--workspace"]]
    [run] = plan([root, fixture], wants(repo, ["fixture"]), repo)
    assert (run.cwd, run.selectors) == ("fixture", ["--workspace"])
    # a path inside it is a run in it too: the target is looked up in every workspace, not the first
    [run] = plan([root, fixture], wants(repo, ["fixture/src/lib:tests::adds_zero"]), repo)
    assert (run.cwd, run.selectors) == ("fixture", ["-p", "fixture", "--lib"])


@needs_rust_parser
def test_a_directory_whose_manifest_cargo_cannot_read_is_refused_with_cargos_reason_not_run_to_fail(fake_cargo):
    repo = repo_of("nested_all")
    broken = {"stray": "error: current package believes it's in a workspace when it's not"}
    with pytest.raises(ValueError, match="cargo cannot read stray/Cargo.toml: error: current package believes"):
        plan([space("nested_all")], parse_wants(repo, ["stray"]), repo, broken)
    with pytest.raises(ValueError, match="cargo cannot read stray/Cargo.toml"):
        plan([space("nested_all")], wants(repo, ["stray/src/lib:tests::adds"]), repo, broken)


@needs_rust_parser
def test_a_directory_with_no_crate_source_in_it_is_refused_and_one_with_modules_is_left_to_the_list(make_repo):
    repo = make_repo(
        {"Cargo.toml": '[package]\nname = "p"\nversion = "0.1.0"\n', "src/lib.rs": "pub mod util;\n", "src/util/mod.rs": "pub fn g() {}\n", "docs/readme.txt": "x"}
    )
    with pytest.raises(ValueError, match="no source of a library, binary, example or test target is below it"):
        parse_wants(repo, ["docs"])
    [pick] = parse_wants(repo, ["src/util"]).picks
    assert pick.prefixes == (("util",),)


# ---- the whole of run_check, with a cargo that prints a capture --------------------------------------


def test_check_with_no_arguments_in_a_rust_repo_runs_cargo_test_in_the_workspace_and_judges_the_output(fake_cargo):
    log = fake_cargo("fail")
    repo = repo_of("fail")
    result = run_check(repo, profile=RUST)
    [call] = cargo_test_calls(log)
    assert call["argv"] == meta("fail")["argv"][1:]
    assert call["cwd"] == str(repo)
    assert result["passed"] is False
    assert result["failures"] == ["FAILED src/lib.rs::tests::bad: panicked at src/lib.rs:14:9: assertion `left == right` failed"]
    assert result["command"] == " ".join(meta("fail")["argv"])


@needs_rust_parser
def test_check_with_ids_runs_the_planned_command_and_judges_what_it_printed(fake_cargo):
    log = fake_cargo("mixed_lib")
    result = run_check(repo_of("mixed_lib"), tests=["src/lib:tests::same"], profile=RUST)
    assert [c["argv"] for c in cargo_test_calls(log)] == [meta("mixed_lib")["argv"][1:]]
    assert [c["argv"][-3:] for c in list_calls(log)] == [["--lib", "--", "--list"]]  # asked what the binary holds first
    assert result["passed"] is True and result["counts"]["passed"] == 1


@needs_rust_parser
def test_check_with_two_targets_runs_cargo_once_per_target_and_says_so(fake_cargo):
    log = fake_cargo("mixed_common_it", "mixed_common_other")
    result = run_check(repo_of("mixed_common_it"), tests=["tests/common/mod.rs"], profile=RUST)
    assert len(cargo_test_calls(log)) == 2 and len(list_calls(log)) == 2
    assert result["passed"] is True and result["counts"]["passed"] == 2
    assert any("2 `cargo test` runs" in n for n in result["notes"])


@needs_rust_parser
def test_a_name_the_list_does_not_have_is_no_test_of_that_name_and_nothing_runs_through_the_whole_path(fake_cargo):
    log = fake_cargo("gated_slow")
    result = run_check(repo_of("gated_slow"), tests=["src/lib:tests::slow"], profile=RUST)
    assert result["passed"] is False and result["exit_code"] == 0
    assert any("no test named tests::slow exists in gated" in p for p in result["problems"])
    assert cargo_test_calls(log) == []  # nothing was listed to run: no test of that name exists


def test_an_explicit_command_still_means_that_command_and_the_generic_reading(fake_cargo):
    log = fake_cargo("pass")
    result = run_check(repo_of("pass"), command=[sys.executable, "-c", "print('1 passed')"], profile=RUST)
    assert calls(log) == []
    assert result["passed"] is True and "counts" not in result and result["summary"] == "1 passed"


def test_without_cargo_the_error_says_so_after_the_arguments_are_checked(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ValueError, match="`cargo` is not on PATH"):
        run_check(repo_of("pass"), profile=RUST)
    with pytest.raises(ValueError, match="not a file or directory|is not a file"):
        run_check(repo_of("pass"), tests=["nope.rs"], profile=RUST)


def test_a_repo_with_no_cargo_manifest_says_where_cargo_is_not_known(fake_cargo, make_repo):
    fake_cargo("pass")
    with pytest.raises(ValueError, match="no Cargo.toml in the repository"):
        run_check(make_repo({"main.rs": "fn main() {}\n"}), profile=RUST)


def test_the_seam_is_a_runner_per_profile_name_and_go_python_are_untouched():
    assert RUNNERS["rust"] == ".rs_check" and RUNNERS["go"] == ".go_check"
    assert runner_for(RUST).run.__module__ == "ripple.rs_check"
    assert runner_for(GO).run.__module__ == "ripple.go_check"
    assert runner_for(PYTHON) is None


# ---- what the environment and the repo can change is named in the verdict ----------------------------


def test_environment_that_changes_what_is_built_or_how_libtest_prints_is_named(fake_cargo, monkeypatch):
    fake_cargo("pass")
    monkeypatch.setenv("RUSTFLAGS", "-Dwarnings")
    monkeypatch.setenv("RUST_TEST_NOCAPTURE", "1")
    monkeypatch.setenv("CARGO_BUILD_TARGET", "wasm32-unknown-unknown")
    monkeypatch.setenv("CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER", "qemu")
    notes = run_check(repo_of("pass"), profile=RUST)["notes"]
    for key in ("RUSTFLAGS", "RUST_TEST_NOCAPTURE", "CARGO_BUILD_TARGET", "RUNNER"):
        assert any(key in n for n in notes), (key, notes)


def test_a_toolchain_file_and_a_cargo_config_of_the_repo_are_named(fake_cargo, tmp_path):
    import shutil

    repo = tmp_path / "pass"
    shutil.copytree(repo_of("pass"), repo)
    (repo / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "stable"\n')
    (repo / ".cargo").mkdir()
    (repo / ".cargo" / "config.toml").write_text('[build]\nrustflags = ["-Dwarnings"]\n[target.x86_64-unknown-linux-gnu]\nrunner = "qemu"\n[alias]\ntest = "test --release"\n')
    fake_cargo("pass")
    notes = run_check(repo, profile=RUST)["notes"]
    assert any("rust-toolchain.toml selects the toolchain" in n for n in notes)
    assert any(".cargo/config.toml sets build.rustflags" in n for n in notes)
    assert any(".cargo/config.toml sets a target runner" in n for n in notes)


def test_a_run_in_a_clean_environment_has_no_environment_notes(fake_cargo, monkeypatch):
    for key in list(os.environ):
        if key in ("RUSTFLAGS", "RUST_TEST_NOCAPTURE", "CARGO_BUILD_TARGET", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_RUSTFLAGS", "RUSTC_WRAPPER") or key.endswith("_RUNNER"):
            monkeypatch.delenv(key)
    fake_cargo("pass")
    assert "notes" not in run_check(repo_of("pass"), profile=RUST)


# ---- a file or directory is a module path, and whatever cannot be mapped to one with certainty is the whole target ----


@needs_rust_parser
def test_a_file_whose_tests_a_macro_writes_is_still_a_module_prefix_and_the_list_has_them():
    repo = repo_of("macgen_file")
    [pick] = wants(repo, ["src/util.rs"]).picks
    assert (pick.root, pick.kind, pick.prefixes, pick.names) == ("src/lib.rs", "lib", (("util",),), None)
    [pick] = wants(repo, ["src/sub"]).picks
    assert pick.prefixes == (("sub",),)
    [pick] = wants(repo, ["src"]).picks
    assert pick.whole  # the crate root is in it


@needs_rust_parser
def test_a_file_declared_with_a_path_attribute_runs_the_whole_target_and_says_why():
    repo = repo_of("pathmod_file")
    request = wants(repo, ["src/elsewhere/x.rs"])
    assert [p.whole for p in request.picks] == [True]
    assert any("the module path of src/elsewhere/x.rs is not certain (a module on the way is declared with #[path])" in n for n in request.notes)
    assert any("so every test of src/lib.rs ran" in n for n in request.notes)


def _crate(make_repo, files):
    repo = make_repo({"Cargo.toml": '[package]\nname = "p"\nversion = "0.1.0"\n', **files})
    model = Model(repo)
    [crate] = [c for c in model.workspace.crates if c.kind == "lib"]
    return model, crate


@pytest.mark.parametrize(
    "files, why",
    [
        ({"src/lib.rs": '#[path = "x.rs"]\nmod a;\n', "src/x.rs": ""}, "#[path]"),
        ({"src/lib.rs": '#[cfg(unix)]\n#[path = "x.rs"]\nmod a;\n#[cfg(not(unix))]\n#[path = "x.rs"]\nmod b;\n', "src/x.rs": ""}, "more than once"),
        ({"src/lib.rs": "mod a;\n", "src/a.rs": 'include!("b.rs");\n', "src/b.rs": ""}, "include!"),
        ({"src/lib.rs": "mod a;\nmake_mods!(mod b;);\n", "src/a.rs": ""}, "a macro in src/lib.rs may declare a module"),
        ({"src/lib.rs": "mod a;\nmod missing;\n", "src/a.rs": ""}, "could not read every module"),
    ],
)
@needs_rust_parser
def test_what_cannot_be_mapped_to_a_module_with_certainty_is_named(make_repo, files, why):
    model, crate = _crate(make_repo, files)
    here = [m for m in model.tree(crate).modules if m.file in ("src/a.rs", "src/x.rs") and not m.inline]
    assert why in (_uncertain(model, crate, here) or "")


@needs_rust_parser
def test_a_plain_module_is_certain_even_with_macros_that_declare_no_module(make_repo):
    model, crate = _crate(make_repo, {"src/lib.rs": "mod a;\nlog!(hello);\n", "src/a.rs": "pub fn f() {}\n"})
    here = [m for m in model.tree(crate).modules if m.file == "src/a.rs"]
    assert _uncertain(model, crate, here) is None


def test_the_prefixes_of_a_directory_drop_those_below_another():
    from ripple.rs_check import _prefixes

    assert _prefixes([("a", "b"), ("a",), ("c",), ("a", "b", "d")]) == (("a",), ("c",))


# ---- the list resolves the picks ---------------------------------------------------------------------------


def _inv(*picks):
    return Invocation("", ["-p", "p", "--lib"], (), list(picks), None, True)


LISTED = ["tests::plain", "util::tests::a", "util::tests::b", "util::inner::tests::c", "utility::x", "tests::r#match"]


def test_a_name_is_resolved_to_the_listed_name_and_one_that_is_not_listed_is_a_problem():
    res = resolve(_inv(Pick("g", "r", "lib", ("tests::plain", "tests::nope"))), LISTED)
    assert res.names == ("tests::plain",) and res.total == 6
    assert res.problems == ["g: no test named tests::nope exists in the target (its test binary lists 6 tests)"]


def test_a_module_prefix_is_every_listed_test_below_it_and_not_a_module_whose_name_starts_the_same():
    res = resolve(_inv(Pick("g", "r", "lib", None, (("util",),))), LISTED)
    assert res.names == ("util::inner::tests::c", "util::tests::a", "util::tests::b")  # not utility::x
    assert set(res.expected[0]) == set(res.names)


def test_a_prefix_with_nothing_below_it_is_a_problem_naming_the_module():
    res = resolve(_inv(Pick("g", "r", "lib", None, (("nothing",),))), LISTED)
    assert res.names == () and "no test is listed below the module nothing" in res.problems[0]


def test_two_picks_of_one_target_share_one_run_of_exactly_the_union():
    res = resolve(_inv(Pick("a", "r", "lib", ("tests::plain",)), Pick("b", "r", "lib", None, (("util",),))), LISTED)
    assert res.names == ("tests::plain", "util::inner::tests::c", "util::tests::a", "util::tests::b")
    assert res.expected[0] == ("tests::plain",)


# ---- examples, and a target with its own harness --------------------------------------------------------


@needs_rust_parser
def test_an_example_with_a_test_is_run_with_example_though_cargo_calls_it_test_false():
    for name in ("example_file", "example_id"):
        [run] = planned(repo_of(name), meta(name)["request"], name)
        assert run.selectors == ["-p", "examples_ok", "--example", "demo"]
    assert [t.test for t in space("example_file").packages[0].targets if t.kind == "example"] == [False]


def test_a_prefix_for_a_target_with_its_own_harness_runs_the_whole_target_and_says_so(tmp_path):
    import shutil

    from ripple.rs_check import parse_metadata

    repo = tmp_path / "own"
    shutil.copytree(repo_of("own_harness_ok"), repo)
    metadata = restore(meta("own_harness_ok")["metadata"], "own_harness_ok").replace(str(repo_of("own_harness_ok")), str(repo))
    request = Request([Pick("tests/common", "tests/it.rs", "test", None, (("common",),))])
    [run] = plan([parse_metadata(metadata, repo)], request, repo)
    assert run.picks[0].whole and not run.listing
    assert any("has `harness = false` and no list of tests, so the whole target ran" in n for n in run.notes)


# ---- the whole of run_check: it lists first, runs exactly the list, and judges against it ----------------


@needs_rust_parser
def test_a_file_whose_failing_test_a_macro_writes_fails_through_the_whole_path(fake_cargo):
    log = fake_cargo("macgen_file")
    result = run_check(repo_of("macgen_file"), tests=["src/util.rs"], profile=RUST)
    assert [c["argv"][-5:] for c in list_calls(log)] == [["-p", "macgen", "--lib", "--", "--list"]]
    [call] = cargo_test_calls(log)
    assert call["argv"][-3:] == ["--exact", "util::tests::generated_fails", "util::tests::plain"]
    assert result["passed"] is False
    assert result["failures"] == ["FAILED src/util.rs::tests::generated_fails: panicked at src/util.rs:8:5: assertion `left == right` failed"]


@needs_rust_parser
def test_a_directory_below_the_root_fails_through_the_whole_path_where_a_reading_would_have_passed(fake_cargo):
    fake_cargo("macgen_dir")
    result = run_check(repo_of("macgen_dir"), tests=["src/sub"], profile=RUST)
    assert result["passed"] is False and result["failures"][0].startswith("FAILED src/sub/mod.rs::tests::sub_generated_fails")


@needs_rust_parser
def test_a_binary_that_prints_no_list_is_a_problem_and_nothing_is_run(fake_cargo):
    log = fake_cargo("pre_early")
    result = run_check(repo_of("pre_early"), tests=["tests/early:would_fail"], profile=RUST)
    assert result["passed"] is False
    assert any("printed no list of its tests that adds up" in p for p in result["problems"])
    assert cargo_test_calls(log) == [] and len(list_calls(log)) == 1


@needs_rust_parser
def test_a_forged_result_for_an_id_is_caught_through_the_whole_path(fake_cargo):
    fake_cargo("forge_a")
    result = run_check(repo_of("forge_a"), tests=["tests/it:a"], profile=RUST)
    assert result["passed"] is False and any("filtered out" in p for p in result["problems"])


@needs_rust_parser
def test_a_listing_that_fails_to_build_is_the_verdict_and_no_run_follows(fake_cargo):
    log = fake_cargo("build_failure")  # the first `cargo test` it is asked is the listing, and it prints the build failure
    result = run_check(repo_of("build_failure"), tests=["src/lib:tests::adds"], profile=RUST)
    assert result["passed"] is False and any("build failed" in p and "cannot find value `missing`" in p for p in result["problems"])
    assert cargo_test_calls(log) == [] and len(list_calls(log)) == 1


@needs_rust_parser
def test_only_a_function_with_no_attribute_at_all_is_refused_as_no_test(make_repo):
    # a function with no attribute cannot be a test; one inside a macro call, or with any attribute, can be, so the binary's list decides
    repo = make_repo(
        {
            "Cargo.toml": '[package]\nname = "p"\nversion = "0.1.0"\n',
            "src/lib.rs": (
                "#[cfg(test)]\nmod tests {\n    fn helper() {}\n    proptest! { fn inside_a_macro() {} }\n"
                '    #[cfg_attr(feature = "x", test)]\n    fn conditionally_a_test() {}\n    #[rstest]\n    fn parameterised() {}\n}\n'
            ),
        }
    )
    with pytest.raises(ValueError, match="no attribute, so it is not a test"):
        parse_wants(repo, ["src/lib.rs::tests::helper"])
    for name in ("inside_a_macro", "conditionally_a_test", "parameterised", "written_by_a_macro_elsewhere"):
        [pick] = parse_wants(repo, [f"src/lib.rs::tests::{name}"]).picks
        assert pick.names == (f"tests::{name}",)
