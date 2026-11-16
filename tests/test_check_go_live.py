"""`check` for Go against the real toolchain, end to end: each fixture module is run and judged for real.

Skipped, with the reason, when `go` is not on PATH. Offline: GOPROXY=off, and the modules need nothing.
"""

import json
import re
import shutil
import subprocess

import pytest

from go_check_support import MODULES
from ripple.checker import run_check
from ripple.profile import GO

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="the go toolchain is not on PATH")


@pytest.fixture(autouse=True)
def offline_go(monkeypatch):
    # GOENV=off: the go env file of whoever runs this is not read (an empty GOFLAGS does not stop it); the tests that
    # mean a go env file point GOENV at a temporary one
    env = {"GOPROXY": "off", "GOTOOLCHAIN": "local", "GOFLAGS": "", "GOWORK": "off", "CGO_ENABLED": "0", "GOENV": "off"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def module(tmp_path):
    """Copy a fixture module out of the repo, so `go` runs in a directory ripple's own tree does not contain."""

    def copy(name):
        target = tmp_path / name
        shutil.copytree(MODULES / name, target)
        return target

    return copy


def check(repo, tests=None, timeout=120):
    return run_check(repo, tests=tests, timeout=timeout, profile=GO)


def test_a_passing_module_with_no_arguments_passes(module):
    result = check(module("pass"))
    assert result["passed"] is True, result
    assert result["counts"]["passed"] == 2
    assert result["command"] == "go test -json -count=1 -skip= -list= -c=false -fuzz= -run= ./..."


def test_a_failing_test_fails_and_is_named_so_it_can_be_passed_back(module):
    repo = module("fail")
    result = check(repo)
    assert result["passed"] is False
    assert result["failures"] == ["FAILED calc_test.go::TestBad: calc_test.go:13: want 5, got 4"]
    # the id ripple names is one it accepts: the failing test alone fails, the passing one alone passes
    assert check(repo, ["calc_test.go::TestBad"])["passed"] is False
    assert check(repo, ["calc_test.go::TestAdd"])["passed"] is True


def test_a_build_failure_is_not_a_pass(module):
    result = check(module("build_failure"))
    assert result["passed"] is False
    assert any("undefined: missing" in p for p in result["problems"])


def test_a_package_with_no_test_files_is_not_a_pass(module):
    result = check(module("no_test_files"))
    assert result["passed"] is False
    assert result["problems"] == ["no tests ran (1 package with no test files)"]


def test_a_test_in_a_file_no_build_includes_is_not_a_pass_though_go_exits_zero(module):
    result = check(module("tagged"), ["tagged_test.go::TestTagged"])
    assert result["exit_code"] == 0
    assert result["passed"] is False
    assert "the pattern matched nothing" in result["problems"][0]


def test_a_panic_fails_with_the_panic(module):
    result = check(module("panic"))
    assert result["passed"] is False
    assert result["failures"][0].startswith("FAILED calc_test.go::TestPanics: panic: runtime error")


def test_a_test_binary_that_exits_early_is_not_a_pass(module):
    result = check(module("exit_early"))
    assert result["exit_code"] == 0
    assert result["passed"] is False
    assert "exited without reporting a result" in result["problems"][0]


def test_ripples_timeout_kills_the_run_and_says_so(module):
    result = check(module("timeout"), timeout=5)  # TestSlow sleeps 30s
    assert result["passed"] is False
    assert result["timed_out"] is True
    assert result["duration_s"] < 25
    assert any("still running when it was killed: TestSlow" in p for p in result["problems"]), result["problems"]


def test_subtests_and_same_named_tests_run_exactly_what_was_asked(module):
    repo = module("subtests")
    one = check(repo, ["calc_test.go::TestTable/bad"])
    assert one["passed"] is False and "FAILED calc_test.go::TestTable/bad: calc_test.go:9: bad is bad" in one["failures"]
    assert check(repo, ["calc_test.go::TestTable/with space"])["passed"] is True
    assert check(repo, ["calc_test.go::TestTable/a/b"])["passed"] is True
    assert check(repo, ["calc_test.go::TestTable/nope"])["passed"] is False

    two = module("twopkgs")
    assert check(two, ["a/a_test.go::TestSame"])["passed"] is True  # b's TestSame, which fails, was not run
    assert check(two, ["b/b_test.go::TestSame"])["passed"] is False
    both = check(two, ["a/a_test.go::TestSame", "b/b_test.go::TestOnlyB"])
    assert both["passed"] is True and both["counts"]["passed"] == 2  # not b's TestSame, not a's TestOnlyA


def test_a_skipped_test_asked_for_is_not_a_pass(module):
    repo = module("skipped")
    assert check(repo)["passed"] is True
    assert check(repo, ["calc_test.go::TestSkipped"])["passed"] is False


def test_a_repo_of_two_modules_runs_both(module):
    repo = module("multimod")
    result = check(repo)
    assert result["passed"] is False
    assert result["failures"] == ["FAILED sub/sub_test.go::TestSub: sub_test.go:7: sub is wrong"]
    assert result["counts"]["passed"] == 1  # the root module's test ran too
    assert check(repo, ["sub/sub_test.go::TestSub"])["passed"] is False
    assert check(repo, ["root_test.go::TestRoot"])["passed"] is True


# ---- guards from review ----------------------------------------------------------------------

SILENT = "reported ok but never printed PASS"


@pytest.mark.parametrize("tests", [None, ["."], ["noop", "ok"]])
def test_a_test_binary_that_exits_zero_before_its_tests_is_not_a_pass_in_any_run_shape(module, tests):
    # noop's TestMain and initexit's init exit 0; their tests would fail. ok's test runs and passes.
    result = check(module("silent_exit"), tests)
    assert result["exit_code"] == 0
    assert result["passed"] is False
    assert any("silent_exit/noop" in p and SILENT in p for p in result["problems"]), result["problems"]
    assert result["counts"]["passed"] == 1


def test_a_silent_package_alone_is_not_a_pass(module):
    result = check(module("silent_exit"), ["initexit"])
    assert result["passed"] is False
    assert any(SILENT in p for p in result["problems"])


def test_only_the_requested_grid_cells_run_and_the_siblings_that_would_fail_do_not(module):
    repo = module("grid")
    assert check(repo)["passed"] is False  # x/2 and y/1 fail when everything runs
    result = check(repo, ["calc_test.go::TestGrid/x/1", "calc_test.go::TestGrid/y/2"])
    assert result["passed"] is True, result
    assert result["failures"] == []
    assert result["command"].count("go test") == 2  # one run per parent, not one cross product


def test_a_directory_of_nested_modules_passes_when_they_do(module):
    result = check(module("nestedmods"), ["services"])
    assert result["passed"] is True, result
    assert result["counts"]["passed"] == 2
    assert "services/api" in result["command"] and "./services/..." not in result["command"]


def test_a_directory_with_no_test_to_run_is_not_a_pass(module):
    assert check(module("no_test_files"), ["."])["problems"] == [".: no test ran there"]
    result = check(module("allskip"), ["."])
    assert result["passed"] is False
    assert result["problems"] == [".: no test ran there"]


def test_a_whole_file_leaves_out_an_example_go_never_runs(module):
    result = check(module("example_noout"), ["calc_test.go"])
    assert result["passed"] is True, result
    assert result["counts"]["passed"] == 3  # TestAdd and the two examples that have an output comment
    assert "ExampleAdd_withOutput" in result["command"] and "ExampleAdd)" not in result["command"]


def plain_go(repo, *extra):
    """The `go test -json` events of a plain run (none of ripple's flags), as (exit code, [(package, test, action)])."""
    done = subprocess.run(
        ["go", "test", "-json", "-count=1", *extra, "./..."], cwd=repo, capture_output=True, text=True, timeout=120
    )
    events = [json.loads(line) for line in done.stdout.splitlines()]
    return done.returncode, [(e["Package"], e.get("Test"), e["Action"]) for e in events if "Package" in e]


FAILED_TESTBAD = ["FAILED calc_test.go::TestBad: calc_test.go:13: want 5, got 4"]

# every spelling go reads GOFLAGS in that narrows a run, or stops it from being one: -run, -test.run, quoted (go splits
# GOFLAGS with quoting), -skip, -test.skip, -list and -c; -fuzz is no run of the tests at all
NARROWING_FLAGS = [
    ("-run", "-run=^TestAdd$"),
    ("-test.run", "-test.run=^TestAdd$"),
    ("-run quoted", "'-run=^TestAdd$'"),
    ("-run double quoted", '"-run=^TestAdd$"'),
    ("--run", "--run=^TestAdd$"),
    ("-skip", "-skip=Bad"),
    ("-test.skip", "-test.skip=Bad"),
    ("-list", "-list=."),
    ("-c", "-c"),
]


@pytest.mark.parametrize("flags", [f for _, f in NARROWING_FLAGS], ids=[n for n, _ in NARROWING_FLAGS])
def test_goflags_in_the_environment_never_make_a_pass_of_a_run_that_left_a_failing_test_out(module, monkeypatch, flags):
    repo = module("fail")
    monkeypatch.setenv("GOFLAGS", flags)
    code, events = plain_go(repo)
    # the flag does what it is feared to do: a plain go test exits 0 and TestBad never runs
    assert code == 0 and ("example.com/fail", "TestBad", "fail") not in events, (code, events)
    result = check(repo)
    assert result["passed"] is False, result
    assert result["failures"] == FAILED_TESTBAD
    assert result["counts"]["passed"] == 1 and result["counts"]["failed"] == 1
    assert any(n.startswith(f"GOFLAGS is set ({flags})") for n in result["notes"]), result["notes"]
    # and for one id: a -run ripple builds from an id is what runs
    assert check(repo, ["calc_test.go::TestBad"])["failures"] == FAILED_TESTBAD


@pytest.mark.parametrize("flags", [f for _, f in NARROWING_FLAGS], ids=[n for n, _ in NARROWING_FLAGS])
def test_goflags_in_the_go_env_file_never_make_a_pass_either(module, monkeypatch, tmp_path, flags):
    # `go env -w GOFLAGS=...` writes the go env file and the process environment never shows it. GOENV points at a
    # temporary file: the real one is never written.
    envfile = tmp_path / "goenv"
    envfile.write_text(f"GOFLAGS={flags}\n")
    monkeypatch.setenv("GOENV", str(envfile))
    monkeypatch.delenv("GOFLAGS")
    repo = module("fail")
    code, events = plain_go(repo)
    assert code == 0 and ("example.com/fail", "TestBad", "fail") not in events, (code, events)
    result = check(repo)
    assert result["passed"] is False, result
    assert result["failures"] == FAILED_TESTBAD
    assert any(n.startswith(f"GOFLAGS is set ({flags})") for n in result["notes"]), result["notes"]
    assert check(repo, ["calc_test.go::TestBad"])["failures"] == FAILED_TESTBAD


def test_a_fuzz_flag_in_goflags_is_no_run_of_the_tests_in_place_of_them(module, monkeypatch):
    repo = module("quiet")
    baseline = check(repo)
    monkeypatch.setenv("GOFLAGS", "-fuzz=FuzzX")
    result = check(repo)
    assert result["passed"] is True and result["counts"] == baseline["counts"]
    assert result["failures"] == baseline["failures"] == []


def test_goflags_that_are_your_own_build_configuration_stay_in_force_and_are_a_note(module, monkeypatch):
    repo = module("pass")
    monkeypatch.setenv("GOFLAGS", "-tags=integration -short -vet=off")
    result = check(repo)
    assert result["passed"] is True and result["problems"] == []
    assert result["notes"] == [
        n for n in result["notes"] if n.startswith("GOFLAGS is set (-tags=integration -short -vet=off)")
    ] != []


def test_the_exec_flag_stays_the_users_and_a_run_it_empties_is_not_a_pass(module, monkeypatch):
    monkeypatch.setenv("GOFLAGS", "-exec=true")
    result = check(module("fail"))  # `true` runs no test binary at all
    assert result["passed"] is False
    assert any("reported ok but never printed PASS" in p for p in result["problems"]), result["problems"]


def events_of(repo, *flags):
    """What a `go test -json` run said, with the times left out and the order made not matter (parallel packages)."""
    done = subprocess.run(["go", "test", "-json", "-count=1", *flags, "./..."], cwd=repo, capture_output=True, text=True, timeout=120)
    events = []
    for line in done.stdout.splitlines():
        event = json.loads(line)
        event.pop("Time", None), event.pop("Elapsed", None)
        if "Output" in event:
            event["Output"] = re.sub(r"\(?\d+\.\d+s\)?", "T", event["Output"])
        events.append(json.dumps(event, sort_keys=True))
    return done.returncode, sorted(events), done.stderr


@pytest.mark.parametrize("name", ["pass", "fail", "subtests", "twopkgs", "skipped", "example_noout", "quiet", "allskip", "grid"])
def test_ripples_overrides_change_nothing_in_an_ordinary_run(module, name):
    # the same events and exit code, with the flags ripple adds (-skip= -list= -c=false -fuzz= -run=) and without
    repo = module(name)
    overridden = events_of(repo, "-skip=", "-list=", "-c=false", "-fuzz=", "-run=")
    assert overridden == events_of(repo)
    assert overridden[1], "the run had no events"


# ---- guards from review, round 2 -------------------------------------------------------------


def test_a_testmain_that_prints_pass_and_exits_zero_is_not_a_pass_in_any_run_shape(module):
    repo = module("printpass")
    for tests in (None, ["."], ["printpass"], ["printpass", "ok"]):
        result = check(repo, tests)
        assert result["exit_code"] == 0, tests  # go calls it ok
        assert result["passed"] is False, (tests, result)
        assert any("printpass/printpass" in p and "no tests to run" in p for p in result["problems"]), (tests, result["problems"])
    assert check(repo, ["ok"])["passed"] is True


def test_every_package_that_legitimately_has_no_test_to_run_still_passes(module):
    # benchmarks only, helpers only, an output-less example only, a fuzz target only, a build-excluded test file and
    # a package of build-excluded files only, all beside a package that runs a test
    repo = module("quiet")
    result = check(repo)
    assert result["passed"] is True and result["problems"] == [] and result["failures"] == [], result
    assert any(n.startswith("3 packages passed without running a test") for n in result["notes"]), result["notes"]
    for tests in (["benchonly"], ["helpers"], ["exnoout"], ["fuzzonly"], ["ok", "helpers"], ["."]):
        shown = check(repo, tests)
        assert not any("no tests to run" in p or "never printed PASS" in p for p in shown["problems"]), (tests, shown["problems"])


def test_a_benchmark_is_neither_counted_nor_an_unfinished_test_under_goflags_bench(module, monkeypatch):
    repo = module("quiet")
    baseline = check(repo)
    monkeypatch.setenv("GOFLAGS", "-bench=. -benchtime=1x")
    result = check(repo)
    assert result["failures"] == [] and not any("did not finish" in p or "Benchmark" in p for p in result["problems"])
    assert result["counts"] == baseline["counts"]
    assert any(n.startswith("GOFLAGS is set (-bench=. -benchtime=1x)") for n in result["notes"])


def test_a_benchmark_that_fails_under_goflags_bench_is_still_a_failure(module, monkeypatch):
    monkeypatch.setenv("GOFLAGS", "-bench=. -benchtime=1x")
    result = check(module("bench_bad"))
    assert result["passed"] is False
    assert result["failures"] == ["FAILED example.com/bench_bad::BenchmarkBad: BenchmarkBad"], result
    assert check(module("bench_ok"))["counts"]["skipped"] == 0  # a skipped benchmark is not a skipped test


def test_a_directory_of_nested_modules_and_a_file_go_leaves_out_passes_when_the_nested_runs_do(module):
    result = check(module("nested_excluded"), ["services"])
    assert result["passed"] is True and result["problems"] == [], result
    assert result["counts"]["passed"] == 2 and result["exit_code"] == 0
    assert result["command"].count("go test") == 3  # the holder's ./services/... too, empty


def test_a_directory_run_in_the_holder_that_matched_no_packages_with_nothing_below_it_run_is_still_a_problem(module, tmp_path):
    # services/ holds a file go leaves out and no nested module: ./services/... matches nothing and nothing ran below
    only = tmp_path / "only"
    (only / "services").mkdir(parents=True)
    (only / "go.mod").write_text("module example.com/only\n\ngo 1.21\n")
    (only / "services" / "gen.go").write_text("//go:build ignore\n\npackage main\n")
    result = check(only, ["services"])
    assert result["passed"] is False
    assert any("matched no packages" in p for p in result["problems"]), result["problems"]
    # a nested module with no package of its own ran nothing either
    nested = tmp_path / "nested"
    (nested / "services" / "api").mkdir(parents=True)
    (nested / "go.mod").write_text("module example.com/nested\n\ngo 1.21\n")
    (nested / "services" / "gen.go").write_text("//go:build ignore\n\npackage main\n")
    (nested / "services" / "api" / "go.mod").write_text("module example.com/nested/services/api\n\ngo 1.21\n")
    result = check(nested, ["services"])
    assert result["passed"] is False, result


def test_two_failing_cells_of_one_grid_are_counted_and_listed_once(module):
    result = check(module("grid"), ["calc_test.go::TestGrid/x/2", "calc_test.go::TestGrid/y/1"])
    assert result["passed"] is False
    assert result["command"].count("go test") == 2
    assert result["counts"]["failed"] == 1 and result["counts"]["subtests"] == 4 and result["counts"]["packages"] == 1
    assert result["summary"] == "0 passed, 1 failed, 0 skipped (and 4 subtests) in 1 package"
    assert result["failures"].count("FAILED calc_test.go::TestGrid") == 1
    assert result["failure_count"] == 5 and len(set(result["failures"])) == 5


def test_a_parent_that_passes_in_one_split_run_and_fails_in_the_other_is_a_failure(module):
    repo = module("grid")
    for tests in (["calc_test.go::TestGrid/x/1", "calc_test.go::TestGrid/y/1"], ["calc_test.go::TestGrid/y/1", "calc_test.go::TestGrid/x/1"]):
        result = check(repo, tests)
        assert result["passed"] is False, result
        assert result["counts"]["passed"] == 0 and result["counts"]["failed"] == 1
        assert "FAILED calc_test.go::TestGrid/y/1: calc_test.go:11: y/1 was not asked for" in result["failures"]


def test_a_name_or_path_the_file_system_cannot_hold_is_a_refusal_not_an_exception(module):
    repo = module("pass")
    for given in (
        "a" * 300,
        "x/" * 3000 + "a_test.go",
        "d" * 300 + "/a_test.go::TestAdd",
        "calc_test.go::" + "T" * 300,
        "example.com/pass:" + "T" * 5000,
        "a\x00b",
        "caf\udce9/x_test.go",
    ):
        with pytest.raises(ValueError):
            check(repo, [given])
