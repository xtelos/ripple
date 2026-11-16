"""What `check` asks `go test` to run for Go: the argv for each request, and the arguments it refuses (no Go needed)."""

import os
import stat
import sys

import pytest

from go_check_support import BASE, BASE_ARGS, CAPTURES, MODULES, go_argv, meta, names
from ripple import checker
from ripple.checker import RUNNERS, run_check, runner_for
from ripple.go_check import find_modules, package_dir, parse_wants, plan, quote_meta
from ripple.profile import GO, PYTHON, TYPESCRIPT

TEST_FILE = 'package {pkg}\n\nimport "testing"\n\n{funcs}\n'


def go_tests(*funcs):
    return "".join(f"func {name}(t *testing.T) {{}}\n" for name in funcs)


def go_repo(make_repo, files):
    """A repo with a go.mod at the root and the given files ({path: text}); package names are not checked, only the layout."""
    return make_repo({"go.mod": "module example.com/r\n\ngo 1.21\n", **files})


def argvs(repo, tests):
    return [run.argv() for run in plan(find_modules(repo), parse_wants(repo, tests))]


def source_with(*funcs):
    return TEST_FILE.format(pkg="p", funcs=go_tests(*funcs))


# ---- every capture's command is the one ripple builds for its request ---------------------------


@pytest.mark.parametrize("name", [n for n in names() if not meta(n)["by_hand"]])
def test_ripple_builds_the_command_each_capture_recorded(name):
    data = meta(name)
    repo = MODULES / data["module"]
    runs = plan(find_modules(repo), parse_wants(repo, data["request"] or []), repo)
    cwd = "" if data["cwd"] == "." else data["cwd"]
    assert [run.argv() for run in runs if run.module.dir == cwd] == [data["argv"]]
    if data["request"]:
        # every run the planner makes for the request was captured (a directory of two nested modules is two runs, and
        # the holder's run over a directory that has only files go leaves out is a third, captured by hand)
        recorded = [(meta(n)["cwd"], meta(n)["argv"]) for n in names() if meta(n)["module"] == data["module"]]
        assert [(run.module.dir or ".", run.argv()) for run in runs if (run.module.dir or ".", run.argv()) not in recorded] == []


def test_the_package_directory_ripple_reads_from_an_import_path_is_the_one_go_list_gave():
    for name in names():
        data = meta(name)
        repo = MODULES / data["module"]
        cwd = "" if data["cwd"] == "." else data["cwd"]
        module = next(m for m in find_modules(repo) if m.dir == cwd)
        for import_path, directory in data["directories"].items():
            assert package_dir(module, import_path) == ("" if directory == "." else directory), (name, import_path)


# ---- planning ----------------------------------------------------------------------------------


def test_no_arguments_runs_every_module_whole(make_repo):
    repo = go_repo(make_repo, {"sub/go.mod": "module example.com/r/sub\n", "vendor/x/go.mod": "module x\n", "testdata/go.mod": "module y\n"})
    assert argvs(repo, []) == [go_argv("./..."), go_argv("./...")]
    assert [run.module.dir for run in plan(find_modules(repo), [])] == ["", "sub"]  # vendor and testdata are not modules


def test_ids_of_one_package_share_one_pattern(make_repo):
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA", "TestB", "TestC")})
    assert argvs(repo, ["a_test.go::TestA", "a_test.go::TestB"]) == [go_argv(".", run="^(TestA|TestB)$")]


def test_the_same_test_name_in_two_packages_runs_only_where_it_was_asked_for(make_repo):
    repo = go_repo(
        make_repo,
        {"a/a_test.go": source_with("TestSame", "TestA"), "b/b_test.go": source_with("TestSame", "TestB")},
    )
    # different tests asked of each package: a pattern for each, never one pattern over both
    assert argvs(repo, ["a/a_test.go::TestSame", "b/b_test.go::TestB"]) == [
        go_argv("./b", run="^(TestB)$"),
        go_argv("./a", run="^(TestSame)$"),
    ]
    # the same test asked of both: one run over both packages, which is what was asked
    assert argvs(repo, ["a/a_test.go::TestSame", "b/b_test.go::TestSame"]) == [go_argv("./a", "./b", run="^(TestSame)$")]


def test_subtests_are_levels_of_the_pattern_and_quoted(make_repo):
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA", "TestB")})
    assert argvs(repo, ["a_test.go::TestA/x", "a_test.go::TestA/y.z"]) == [go_argv(".", run="^TestA$/^(x|y\\.z)$")]
    assert argvs(repo, ["a_test.go::TestA/with space"]) == [go_argv(".", run="^TestA$/^(with_space)$")]
    assert argvs(repo, ["a_test.go::TestA/a/b"]) == [go_argv(".", run="^TestA$/^a$/^(b)$")]


def test_subtests_of_different_tests_or_depths_never_share_a_pattern(make_repo):
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA", "TestB")})
    runs = argvs(repo, ["a_test.go::TestA/x", "a_test.go::TestA/y/z", "a_test.go::TestB/w"])
    assert sorted(a[a.index("-run") + 1] for a in runs) == ["^TestA$/^(x)$", "^TestA$/^y$/^(z)$", "^TestB$/^(w)$"]


def patterns(repo, tests):
    return sorted(a[a.index("-run") + 1] for a in argvs(repo, tests))


def test_subtests_at_one_depth_are_never_a_cross_product_of_what_was_asked(make_repo):
    # TestGrid/x/1 and TestGrid/y/2 as ^TestGrid$/^(x|y)$/^(1|2)$ would also run x/2 and y/1
    repo = go_repo(make_repo, {"a_test.go": source_with("TestGrid")})
    assert patterns(repo, ["a_test.go::TestGrid/x/1", "a_test.go::TestGrid/y/2"]) == [
        "^TestGrid$/^x$/^(1)$",
        "^TestGrid$/^y$/^(2)$",
    ]
    # the same parent: its last level alternates, which is exactly the union
    assert patterns(repo, ["a_test.go::TestGrid/x/1", "a_test.go::TestGrid/x/2"]) == ["^TestGrid$/^x$/^(1|2)$"]
    # depth four
    assert patterns(repo, ["a_test.go::TestGrid/x/1/a", "a_test.go::TestGrid/x/1/b", "a_test.go::TestGrid/x/2/a"]) == [
        "^TestGrid$/^x$/^1$/^(a|b)$",
        "^TestGrid$/^x$/^2$/^(a)$",
    ]


def test_mixed_depths_are_one_pattern_per_parent_and_a_shallower_ask_covers_a_deeper_one(make_repo):
    repo = go_repo(make_repo, {"a_test.go": source_with("TestGrid", "TestOther")})
    assert patterns(repo, ["a_test.go::TestGrid/x", "a_test.go::TestGrid/y/2", "a_test.go::TestGrid/z/1"]) == [
        "^TestGrid$/^(x)$",
        "^TestGrid$/^y$/^(2)$",
        "^TestGrid$/^z$/^(1)$",
    ]
    # TestGrid/x already runs x/1: asking for both is the shallower one
    assert patterns(repo, ["a_test.go::TestGrid/x", "a_test.go::TestGrid/x/1", "a_test.go::TestOther"]) == [
        "^(TestOther)$",
        "^TestGrid$/^(x)$",
    ]


def test_the_same_cell_asked_of_two_packages_is_one_run_over_both(make_repo):
    repo = go_repo(make_repo, {"a/a_test.go": source_with("TestGrid"), "b/b_test.go": source_with("TestGrid")})
    assert argvs(repo, ["a/a_test.go::TestGrid/x/1", "b/b_test.go::TestGrid/x/1"]) == [
        go_argv("./a", "./b", run="^TestGrid$/^x$/^(1)$")
    ]


def test_a_test_asked_for_whole_already_runs_its_subtests(make_repo):
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA")})
    assert argvs(repo, ["a_test.go::TestA", "a_test.go::TestA/x"]) == [go_argv(".", run="^(TestA)$")]


def test_a_directory_is_every_package_below_it_and_the_root_is_every_module(make_repo):
    repo = go_repo(make_repo, {"pkg/in/x_test.go": source_with("TestX"), "sub/go.mod": "module example.com/r/sub\n"})
    assert argvs(repo, ["pkg"]) == [go_argv("./pkg/...")]
    assert argvs(repo, ["."]) == [go_argv("./..."), go_argv("./...")]
    assert [run.module.dir for run in plan(find_modules(repo), parse_wants(repo, ["sub"]))] == ["sub"]


def test_a_repo_whose_modules_are_all_below_the_root_runs_each_of_them_for_the_root(make_repo):
    repo = make_repo(
        {
            "one/go.mod": "module example.com/one\n",
            "two/go.mod": "module example.com/two\n",
            "docs/readme.txt": "no go here\n",
        }
    )
    assert [run.module.dir for run in plan(find_modules(repo), parse_wants(repo, ["."]))] == ["one", "two"]
    with pytest.raises(ValueError, match="not inside any Go module"):
        plan(find_modules(repo), parse_wants(repo, ["docs"]))


def test_a_test_file_is_the_tests_it_declares(make_repo):
    extra = "func BenchmarkE(b *testing.B) {}\nfunc ExampleC() {\n\t// Output:\n}\nfunc ExampleNoOutput() {\n}\n"
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA", "TestB", "FuzzD", "helper") + extra})
    assert argvs(repo, ["a_test.go"]) == [go_argv(".", run="^(ExampleC|FuzzD|TestA|TestB)$")]


def test_an_id_in_a_nested_module_runs_in_that_module(make_repo):
    repo = go_repo(make_repo, {"sub/go.mod": "module example.com/r/sub\n", "sub/in/x_test.go": source_with("TestX")})
    runs = plan(find_modules(repo), parse_wants(repo, ["sub/in/x_test.go::TestX"]))
    assert [(r.module.dir, r.argv()[-3:]) for r in runs] == [("sub", ["-run", "^(TestX)$", "./in"])]
    assert runs[0].command().startswith("cd sub && go test")


def test_a_directory_named_like_an_option_is_always_given_to_go_with_a_dot_slash(make_repo):
    repo = go_repo(make_repo, {"-v/x_test.go": source_with("TestX")})
    assert argvs(repo, ["./-v"]) == [go_argv("./-v/...")]
    assert argvs(repo, ["./-v/x_test.go::TestX"]) == [go_argv("./-v", run="^(TestX)$")]
    with pytest.raises(ValueError):
        run_check(repo, tests=["-v"], profile=GO)


def test_quote_meta_matches_gos_regexp_quote_meta():
    assert quote_meta("a.b*c+d?(e)|f[g]{h}^i$j\\k") == "a\\.b\\*c\\+d\\?\\(e\\)\\|f\\[g\\]\\{h\\}\\^i\\$j\\\\k"


# ---- what is refused ---------------------------------------------------------------------------


@pytest.fixture
def never_runs(monkeypatch):
    """Fail the test if anything tries to start a process: a refusal must come before any run."""

    def refuse(*args, **kwargs):
        raise AssertionError("a process was started")

    monkeypatch.setattr(checker, "run_process", refuse)
    monkeypatch.setattr("ripple.go_check.run_process", refuse)


BAD = [
    "-run=TestX", "-count=1", "-exec=/bin/sh", "-v", "--", "-", "-x/a_test.go::TestA", "@opts.txt", "@/etc/hostname", "",
    "../outside_test.go", "../outside_test.go::TestOut", "/etc", "/etc/passwd", "outside_link", "outside_link/x_test.go",
    "a_test.go::-run", "a_test.go::TestNone", "a_test.go::", "a_test.go::TestA/", "a_test.go::TestA//x", "a_test.go::TestA/x\ny",
    "a_test.go::Test\x00A", "missing_test.go::TestA", "plain.go", "plain.go::TestA", "sub::TestA", "a_test.go::testlower",
    "a_test.go::BenchmarkB", "a_test.go::Testa",
]


@pytest.mark.parametrize("bad", BAD)
def test_a_go_argument_that_is_an_option_or_outside_the_repo_or_not_a_test_is_refused_before_anything_runs(
    make_repo, tmp_path_factory, never_runs, bad
):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "x_test.go").write_text(source_with("TestOut"))
    (outside / "outside_test.go").write_text(source_with("TestOut"))
    repo = go_repo(
        make_repo,
        {
            "a_test.go": source_with("TestA", "BenchmarkB") + "func testlower(t *testing.T) {}\n",
            "plain.go": "package p\n",
            "sub/x.go": "package sub\n",
            "opts.txt": "-run=Nothing\n",
        },
    )
    os.symlink(outside, repo / "outside_link")
    with pytest.raises(ValueError):
        run_check(repo, tests=[bad], profile=GO)


UNUSABLE = {
    "a name over NAME_MAX": "a" * 256,
    "a name over NAME_MAX in a file path": "d" * 300 + "/a_test.go::TestA",
    "a path over PATH_MAX": "x/" * 3000 + "a_test.go",
    "a symbol id over NAME_MAX": "example.com/r:" + "T" * 5000,
    "a long subtest name in a symbol id": "example.com/r:TestA/" + "s" * 300,
    "a NUL": "a\x00b",
    "a NUL in a symbol id": "example.com/r:Test\x00A",
    "a lone surrogate": "caf\udce9/x_test.go",
    "a lone surrogate after ::": "a_test.go::Test\udce9",
}


@pytest.mark.parametrize("given", list(UNUSABLE.values()), ids=list(UNUSABLE))
def test_an_argument_the_file_system_cannot_hold_is_a_refusal_not_an_oserror(make_repo, never_runs, given):
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA")})
    with pytest.raises(ValueError) as refused:
        run_check(repo, tests=[given], profile=GO)
    assert not isinstance(refused.value, OSError)
    assert len(str(refused.value)) < 400  # the refusal does not echo five thousand characters back


def test_a_file_system_error_while_looking_up_an_id_is_a_refusal(make_repo, never_runs, monkeypatch):
    from pathlib import Path

    repo = go_repo(make_repo, {"a_test.go": source_with("TestA")})
    real = Path.exists

    def deny(self):
        if "denied" in str(self):
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "exists", deny)
    with pytest.raises(ValueError, match="cannot be used as a path"):
        run_check(repo, tests=["example.com/r/denied:TestA"], profile=GO)


def test_a_symlinked_test_file_pointing_outside_the_repo_is_refused(make_repo, tmp_path_factory, never_runs):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "x_test.go").write_text(source_with("TestOut"))
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA")})
    os.symlink(outside / "x_test.go", repo / "link_test.go")
    with pytest.raises(ValueError, match="inside"):
        run_check(repo, tests=["link_test.go::TestOut"], profile=GO)


def test_nothing_in_a_go_command_comes_from_an_argument_but_its_package_and_pattern(make_repo):
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA")})
    argv = argvs(repo, ["a_test.go::TestA/-exec=evil", "a_test.go::TestA/--"])[0]
    flags = [a for a in argv if a.startswith("-")]
    assert flags == BASE[2:] + ["-run"]  # the subtest text is the pattern after -run, not an argument of its own
    assert argv[argv.index("-run") + 1].startswith("^TestA$/")


def test_a_repo_with_no_go_mod_is_refused_with_the_way_out(make_repo, never_runs):
    repo = make_repo({"a_test.go": source_with("TestA")})
    with pytest.raises(ValueError, match="--test-command"):
        run_check(repo, profile=GO)


def test_go_missing_from_path_is_a_message_not_a_crash(make_repo, monkeypatch):
    repo = go_repo(make_repo, {"a_test.go": source_with("TestA")})
    monkeypatch.setattr("ripple.go_check.shutil.which", lambda name: None)
    with pytest.raises(ValueError, match="`go` is not on PATH"):
        run_check(repo, profile=GO)


# ---- the seam, and a fake go on PATH -----------------------------------------------------------

FAKE_GO = """#!{python}
import json, os, sys, time
if sys.argv[1:3] == ["env", "GOFLAGS"]:
    # `go env GOFLAGS`: answered from the environment and never logged as a test run
    time.sleep(float(os.environ.get("FAKE_GO_ENV_SLEEP", "0")))
    sys.stdout.write(os.environ.get("FAKE_GO_ENV_GOFLAGS", os.environ.get("GOFLAGS", "")) + "\\n")
    sys.exit(int(os.environ.get("FAKE_GO_ENV_EXIT", "0")))
with open(os.environ["FAKE_GO_LOG"], "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
sys.stdout.write(open(os.environ["FAKE_GO_STREAM"]).read())
sys.stderr.write(os.environ.get("FAKE_GO_STDERR", ""))
sys.exit(int(os.environ["FAKE_GO_EXIT"]))
"""


@pytest.fixture
def fake_go(tmp_path_factory, monkeypatch):
    """A `go` on PATH that records how it was called and prints a captured stream; returns the log file."""
    bin_dir = tmp_path_factory.mktemp("bin")
    script = bin_dir / "go"
    script.write_text(FAKE_GO.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = bin_dir / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GO_LOG", str(log))

    def use(capture: str):
        monkeypatch.setenv("FAKE_GO_STREAM", str(CAPTURES / f"{capture}.jsonl"))
        monkeypatch.setenv("FAKE_GO_EXIT", str(meta(capture)["exit_code"]))
        return log

    return use


def calls(log):
    import json

    return [json.loads(line) for line in log.read_text().splitlines()]


def test_check_with_no_arguments_in_a_go_repo_runs_go_test_json_in_the_module_and_judges_the_stream(fake_go):
    log = fake_go("fail")
    repo = MODULES / "fail"
    result = run_check(repo, profile=GO)
    [call] = calls(log)
    assert call["argv"] == go_argv("./...", program=False)
    assert call["cwd"] == str(repo)
    assert result["passed"] is False
    assert result["failures"] == ["FAILED calc_test.go::TestBad: calc_test.go:13: want 5, got 4"]
    assert result["command"] == "go test -json -count=1 -skip= -list= -c=false -fuzz= -run= ./..."


def test_check_with_ids_runs_the_planned_command(fake_go):
    log = fake_go("twopkgs_a_only")
    result = run_check(MODULES / "twopkgs", tests=["a/a_test.go::TestSame"], profile=GO)
    assert calls(log)[0]["argv"] == go_argv("./a", run="^(TestSame)$", program=False)
    assert result["passed"] is True


def test_check_in_a_nested_module_runs_from_that_module(fake_go):
    log = fake_go("multimod_sub")
    result = run_check(MODULES / "multimod", tests=["sub"], profile=GO)
    assert calls(log)[0]["cwd"] == str(MODULES / "multimod" / "sub")
    assert result["failures"] == ["FAILED sub/sub_test.go::TestSub: sub_test.go:7: sub is wrong"]


def test_a_stderr_line_does_not_corrupt_the_stream(fake_go, monkeypatch):
    fake_go("pass")
    monkeypatch.setenv("FAKE_GO_STDERR", "go: warning: something\n")
    assert run_check(MODULES / "pass", profile=GO)["passed"] is True


def test_no_event_at_all_with_stderr_says_what_go_said(fake_go, tmp_path, monkeypatch):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    fake_go("pass")
    monkeypatch.setenv("FAKE_GO_STREAM", str(empty))
    monkeypatch.setenv("FAKE_GO_EXIT", "1")
    monkeypatch.setenv("FAKE_GO_STDERR", "go: cannot find main module\n")
    result = run_check(MODULES / "pass", profile=GO)
    assert result["passed"] is False
    assert any("go: cannot find main module" in p for p in result["problems"])


def test_an_explicit_command_still_means_that_command_and_the_generic_reading(fake_go):
    log = fake_go("pass")
    result = run_check(MODULES / "pass", command=[sys.executable, "-c", "print('1 passed')"], profile=GO)
    assert not log.exists()
    assert result["passed"] is True
    assert "counts" not in result and result["summary"] == "1 passed"


def test_the_seam_is_a_runner_per_profile_name_and_python_and_typescript_have_none():
    assert RUNNERS == {"go": ".go_check"}
    assert runner_for(GO).run.__module__ == "ripple.go_check"
    assert runner_for(PYTHON) is None
    assert runner_for(TYPESCRIPT) is None


# ---- a directory that holds only nested modules ----------------------------------------------


def test_a_directory_of_nested_modules_is_run_in_each_of_them_and_never_in_the_holder(make_repo):
    repo = MODULES / "nestedmods"
    runs = plan(find_modules(repo), parse_wants(repo, ["services"]), repo)
    assert [(r.module.dir, r.argv()[len(BASE) + 1:]) for r in runs] == [("services/api", ["./..."]), ("services/web", ["./..."])]


def test_the_holder_runs_the_directory_when_it_has_a_package_below_it_outside_the_nested_modules(make_repo):
    repo = go_repo(
        make_repo,
        {
            "services/shared/x.go": "package shared\n",
            "services/api/go.mod": "module example.com/r/services/api\n",
            "services/api/api.go": "package api\n",
        },
    )
    runs = plan(find_modules(repo), parse_wants(repo, ["services"]), repo)
    assert [(r.module.dir, r.argv()[len(BASE) + 1:]) for r in runs] == [("", ["./services/..."]), ("services/api", ["./..."])]


@pytest.mark.parametrize("ignored", ["services/testdata/x.go", "services/_scratch/x.go", "services/.hidden/x.go", "services/vendor/x.go", "services/_x.go"])
def test_a_go_file_the_go_tool_does_not_read_is_not_a_package_for_the_holder(make_repo, ignored):
    repo = go_repo(make_repo, {ignored: "package x\n", "services/api/go.mod": "module example.com/r/services/api\n"})
    runs = plan(find_modules(repo), parse_wants(repo, ["services"]), repo)
    assert [r.module.dir for r in runs] == ["services/api"]


def test_check_on_a_directory_of_nested_modules_never_runs_the_holder_over_it(fake_go):
    log = fake_go("pass")
    run_check(MODULES / "nestedmods", tests=["services"], profile=GO)
    assert [(call["cwd"].removeprefix(str(MODULES / "nestedmods")), call["argv"][-1]) for call in calls(log)] == [
        ("/services/api", "./..."),
        ("/services/web", "./..."),
    ]


# ---- GOFLAGS reaches every go test -----------------------------------------------------------


def goflags_check(fake_go, monkeypatch, flags, tests=None):
    fake_go("pass")
    monkeypatch.setenv("GOFLAGS", flags)
    return run_check(MODULES / "pass", tests=tests, profile=GO)


NARROWING = [
    "-run=^TestAdd$", "-test.run=^TestAdd$", "'-run=^TestAdd$'", '"-run=^TestAdd$"', "--run=^TestAdd$", "-skip=TestAddZero",
    "-test.skip=Bad",
    "-list=.", "-c", "-fuzz=FuzzX",
]  # fmt: skip


@pytest.mark.parametrize("flags", NARROWING)
def test_what_goflags_could_narrow_a_run_with_is_overridden_on_the_command_line_in_every_spelling(fake_go, monkeypatch, flags):
    # the live tests (test_check_go_live.py) show go running the failing test; here, that the command carries the override
    log = fake_go("pass")
    monkeypatch.setenv("GOFLAGS", flags)
    result = run_check(MODULES / "pass", profile=GO)
    assert [c["argv"] for c in calls(log)] == [go_argv("./...", program=False)]
    assert result["command"] == "go test -json -count=1 -skip= -list= -c=false -fuzz= -run= ./..."
    assert result["passed"] is True
    assert any(n.startswith(f"GOFLAGS is set ({flags})") for n in result["notes"])


def test_goflags_never_replace_a_run_pattern_ripple_gave_the_run(fake_go, monkeypatch):
    log = fake_go("pass")
    monkeypatch.setenv("GOFLAGS", "-run=^Nothing$")
    result = run_check(MODULES / "pass", tests=["calc_test.go::TestAdd"], profile=GO)
    assert [c["argv"] for c in calls(log)] == [go_argv(".", run="^(TestAdd)$", program=False)]
    assert "-run=" not in calls(log)[0]["argv"]  # only a run with no pattern of its own says -run=
    assert result["passed"] is True


@pytest.mark.parametrize("flags", ["-tags=integration", "-short -race", "--run=", "-count=3", "-vet=off", "-exec=true"])
def test_goflags_that_are_your_build_configuration_are_a_note_and_nothing_more(fake_go, monkeypatch, flags):
    result = goflags_check(fake_go, monkeypatch, flags)
    assert result["passed"] is True
    assert result["problems"] == []
    assert any(n.startswith(f"GOFLAGS is set ({flags})") for n in result["notes"])


def test_the_note_reads_go_env_goflags_so_a_go_env_file_shows_too(fake_go, monkeypatch):
    # the process environment has no GOFLAGS, the go env file (what `go env -w` writes) does: go env says it
    fake_go("pass")
    monkeypatch.delenv("GOFLAGS", raising=False)
    monkeypatch.setenv("FAKE_GO_ENV_GOFLAGS", "-run=^TestAdd$")
    result = run_check(MODULES / "pass", profile=GO)
    assert any(n.startswith("GOFLAGS is set (-run=^TestAdd$)") for n in result["notes"])


def test_go_env_is_never_a_test_run(fake_go, monkeypatch):
    log = fake_go("pass")
    monkeypatch.setenv("GOFLAGS", "-short")
    run_check(MODULES / "pass", profile=GO)
    assert len(calls(log)) == 1 and calls(log)[0]["argv"][0] == "test"


def test_no_goflags_no_note(fake_go, monkeypatch):
    result = goflags_check(fake_go, monkeypatch, "  ")
    assert result["passed"] is True
    assert "notes" not in result


def test_a_go_env_that_fails_is_no_note_and_not_a_failed_check(fake_go, monkeypatch):
    fake_go("pass")
    monkeypatch.setenv("GOFLAGS", "-short")
    monkeypatch.setenv("FAKE_GO_ENV_EXIT", "2")
    result = run_check(MODULES / "pass", profile=GO)
    assert result["passed"] is True and "notes" not in result


def test_a_go_env_that_hangs_is_cut_off_with_the_check_s_own_timeout_and_is_no_note(fake_go, monkeypatch):
    import time

    fake_go("pass")
    monkeypatch.setenv("GOFLAGS", "-short")
    monkeypatch.setenv("FAKE_GO_ENV_SLEEP", "60")
    start = time.monotonic()
    result = run_check(MODULES / "pass", profile=GO, timeout=3)
    assert time.monotonic() - start < 8, "go env GOFLAGS held the check up past its timeout"
    assert result["passed"] is True and "notes" not in result


def test_go_env_is_not_asked_once_the_timeout_is_used_up(fake_go, monkeypatch):
    # a go env with no budget left is skipped (a run that began is judged however long `go env` would take)
    from ripple import go_check

    assert go_check._go_env_flags("/nonexistent/go", MODULES / "pass", 0) == ""
    assert go_check._go_env_flags("/nonexistent/go", MODULES / "pass", 5) == ""  # a go that cannot start is no note
