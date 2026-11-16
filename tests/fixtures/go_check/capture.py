"""Write the tiny Go modules in this directory and capture a real `go test -json` stream from each.

Run by hand with Go installed (GOPROXY=off, nothing is downloaded):

    GOROOT=/path/to/go python3 tests/fixtures/go_check/capture.py [go]

The modules are written under modules/, one directory each. Every run is then made from the
directory in `cwd` with the exact argv in `argv` (the command ripple's Go runner must build for
the same request), and captures/<name>.jsonl holds the stream byte for byte, captures/<name>.json
the rest: argv, cwd, the exit code, stderr, the `go list` answer (import path to directory, as
the runner reads it) and `request`, the `tests` argument that asks for it (null: no arguments).
tests/test_check_go_streams.py parses the streams without Go and tests/test_check_go_plan.py
checks that ripple builds each argv from its request; tests/test_check_go_live.py runs the
toolchain.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULES = HERE / "modules"
CAPTURES = HERE / "captures"
# Runs happen in a copy here, so no stream carries the path of whoever captured it.
WORK = Path("/tmp/ripple-go-check-capture")

SMALL = {"calc.go": "package calc\n\nfunc Add(a, b int) int { return a + b }\n"}


def mod(name: str) -> str:
    return f"module example.com/{name}\n\ngo 1.21\n"


def test_file(*bodies: str, imports: str = '"testing"') -> str:
    return f"package calc\n\nimport {imports}\n\n" + "\n".join(bodies)


PASSING = "func TestAdd(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fatal(\"1+2\")\n\t}\n}\n"
PASSING_TWO = "func TestAddZero(t *testing.T) {\n\tif Add(0, 0) != 0 {\n\t\tt.Fatal(\"0+0\")\n\t}\n}\n"
FAILING = "func TestBad(t *testing.T) {\n\tif Add(2, 2) != 5 {\n\t\tt.Errorf(\"want 5, got %d\", Add(2, 2))\n\t}\n}\n"

SOURCES: dict[str, dict[str, str]] = {
    "pass": {"go.mod": mod("pass"), **SMALL, "calc_test.go": test_file(PASSING, PASSING_TWO)},
    "fail": {"go.mod": mod("fail"), **SMALL, "calc_test.go": test_file(PASSING, FAILING)},
    "build_failure": {
        "go.mod": mod("build_failure"),
        "calc.go": "package calc\n\nfunc Add(a, b int) int { return a + missing }\n",
        "calc_test.go": test_file(PASSING),
        "ok/ok.go": "package ok\n\nfunc One() int { return 1 }\n",
        "ok/ok_test.go": 'package ok\n\nimport "testing"\n\nfunc TestOne(t *testing.T) {\n\tif One() != 1 {\n\t\tt.Fatal("one")\n\t}\n}\n',
    },
    "test_build_failure": {
        "go.mod": mod("test_build_failure"),
        **SMALL,
        "calc_test.go": test_file("func TestAdd(t *testing.T) {\n\tAddd(1, 2)\n}\n"),
    },
    "vet_failure": {
        "go.mod": mod("vet_failure"),
        **SMALL,
        "calc_test.go": test_file(
            'func TestPrint(t *testing.T) {\n\tt.Logf("%d", "not a number")\n}\n',
        ),
    },
    "tagged": {
        "go.mod": mod("tagged"),
        **SMALL,
        "calc_test.go": test_file(PASSING),
        "tagged_test.go": "//go:build integration\n\n" + test_file("func TestTagged(t *testing.T) {\n}\n"),
    },
    "no_test_files": {"go.mod": mod("no_test_files"), **SMALL},
    "nomatch": {"go.mod": mod("nomatch"), **SMALL, "calc_test.go": test_file(PASSING, PASSING_TWO)},
    "panic": {
        "go.mod": mod("panic"),
        **SMALL,
        "calc_test.go": test_file(
            "func TestAfter(t *testing.T) {\n}\n",
            'func TestPanics(t *testing.T) {\n\tvar xs []int\n\t_ = xs[Add(1, 2)]\n}\n',
        ),
    },
    "timeout": {
        "go.mod": mod("timeout"),
        **SMALL,
        "calc_test.go": test_file(
            "func TestFast(t *testing.T) {\n}\n",
            "func TestSlow(t *testing.T) {\n\ttime.Sleep(30 * time.Second)\n}\n",
            imports='(\n\t"testing"\n\t"time"\n)',
        ),
    },
    "subtests": {
        "go.mod": mod("subtests"),
        **SMALL,
        "calc_test.go": test_file(
            "func TestTable(t *testing.T) {\n"
            '\tfor _, name := range []string{"ok_case", "with space", "a/b", "bad", "bad2"} {\n'
            "\t\tt.Run(name, func(t *testing.T) {\n"
            '\t\t\tif len(name) >= 3 && name[:3] == "bad" {\n\t\t\t\tt.Errorf("%s is bad", name)\n\t\t\t}\n'
            "\t\t})\n\t}\n}\n",
            "func TestOther(t *testing.T) {\n}\n",
        ),
    },
    "twopkgs": {
        "go.mod": mod("twopkgs"),
        "a/a.go": "package a\n\nfunc One() int { return 1 }\n",
        "a/a_test.go": 'package a\n\nimport "testing"\n\nfunc TestSame(t *testing.T) {\n\tif One() != 1 {\n\t\tt.Fatal("a")\n\t}\n}\n\nfunc TestOnlyA(t *testing.T) {\n}\n',
        "b/b.go": "package b\n\nfunc Two() int { return 2 }\n",
        "b/b_test.go": 'package b\n\nimport "testing"\n\nfunc TestSame(t *testing.T) {\n\tif Two() != 3 {\n\t\tt.Error("b is wrong")\n\t}\n}\n\nfunc TestOnlyB(t *testing.T) {\n}\n',
    },
    "skipped": {
        "go.mod": mod("skipped"),
        **SMALL,
        "calc_test.go": test_file(
            'func TestSkipped(t *testing.T) {\n\tt.Skip("not today")\n}\n',
            PASSING,
        ),
    },
    "exit_early": {
        "go.mod": mod("exit_early"),
        **SMALL,
        "calc_test.go": test_file(
            "func TestBefore(t *testing.T) {\n}\n",
            "func TestExits(t *testing.T) {\n\tsyscall.Exit(0)\n}\n",
            "func TestAfter(t *testing.T) {\n}\n",
            imports='(\n\t"syscall"\n\t"testing"\n)',
        ),
    },
    "killed": {
        "go.mod": mod("killed"),
        **SMALL,
        "calc_test.go": test_file(
            "func TestBefore(t *testing.T) {\n}\n",
            "func TestKilled(t *testing.T) {\n\tsyscall.Kill(syscall.Getpid(), syscall.SIGKILL)\n\ttime.Sleep(10 * time.Second)\n}\n",
            imports='(\n\t"syscall"\n\t"testing"\n\t"time"\n)',
        ),
    },
    "testmain_noop": {
        "go.mod": mod("testmain_noop"),
        **SMALL,
        "calc_test.go": test_file(
            "func TestMain(m *testing.M) {\n\tos.Exit(0)\n}\n",
            "func TestNeverRuns(t *testing.T) {\n\tt.Fatal(\"never\")\n}\n",
            imports='(\n\t"os"\n\t"testing"\n)',
        ),
    },
    # Two packages whose test binaries exit 0 before any test runs (TestMain, and an init in a test file), beside one
    # that runs. Each hides a failing test: a verdict of passed would be a lie.
    "silent_exit": {
        "go.mod": mod("silent_exit"),
        "noop/noop.go": "package noop\n",
        "noop/noop_test.go": 'package noop\n\nimport (\n\t"os"\n\t"testing"\n)\n\nfunc TestMain(m *testing.M) {\n\tos.Exit(0)\n}\n\nfunc TestBroken(t *testing.T) {\n\tt.Fatal("never runs")\n}\n',
        "initexit/initexit.go": "package initexit\n",
        "initexit/initexit_test.go": 'package initexit\n\nimport (\n\t"os"\n\t"testing"\n)\n\nfunc init() {\n\tos.Exit(0)\n}\n\nfunc TestAlsoBroken(t *testing.T) {\n\tt.Fatal("never runs")\n}\n',
        "ok/ok.go": "package ok\n",
        "ok/ok_test.go": "package ok\n\nimport \"testing\"\n\nfunc TestOK(t *testing.T) {\n}\n",
    },
    # Packages that legitimately have no test to run, each beside a package that runs one.
    "quiet": {
        "go.mod": mod("quiet"),
        "ok/ok.go": "package ok\n",
        "ok/ok_test.go": "package ok\n\nimport \"testing\"\n\nfunc TestOK(t *testing.T) {\n}\n",
        "benchonly/b.go": "package benchonly\n",
        "benchonly/b_test.go": "package benchonly\n\nimport \"testing\"\n\nfunc BenchmarkX(b *testing.B) {\n\tfor i := 0; i < b.N; i++ {\n\t}\n}\n",
        "helpers/h.go": "package helpers\n",
        "helpers/h_test.go": "package helpers\n\nfunc helper() int { return 1 }\n",
        "exnoout/e.go": "package exnoout\n\nfunc Hello() {}\n",
        "exnoout/e_test.go": "package exnoout\n\nfunc ExampleHello() {\n\tHello()\n}\n",
        "fuzzonly/f.go": "package fuzzonly\n",
        "fuzzonly/f_test.go": "package fuzzonly\n\nimport \"testing\"\n\nfunc FuzzX(f *testing.F) {\n\tf.Add(1)\n\tf.Fuzz(func(t *testing.T, n int) {})\n}\n",
        "allexcluded/x.go": "//go:build ignore\n\npackage allexcluded\n",
        "excluded/x.go": "package excluded\n",
        "excluded/x_test.go": "//go:build integration\n\npackage excluded\n\nimport \"testing\"\n\nfunc TestNever(t *testing.T) {\n\tt.Fatal(\"never\")\n}\n",
    },
    # benchmarks that run (under -bench): one passes, one skips, and in bench_bad one fails
    "bench_ok": {
        "go.mod": mod("bench_ok"),
        "b.go": "package b\n",
        "b_test.go": 'package b\n\nimport "testing"\n\nfunc TestOK(t *testing.T) {\n}\n\nfunc BenchmarkGood(b *testing.B) {\n\tfor i := 0; i < b.N; i++ {\n\t}\n}\n\nfunc BenchmarkSkipped(b *testing.B) {\n\tb.Skip("no")\n}\n',
    },
    "bench_bad": {
        "go.mod": mod("bench_bad"),
        "b.go": "package b\n",
        "b_test.go": 'package b\n\nimport "testing"\n\nfunc TestOK(t *testing.T) {\n}\n\nfunc BenchmarkBad(b *testing.B) {\n\tb.Fatal("bench broke")\n}\n',
    },
    # printpass prints PASS itself and exits 0 before any test: its output looks like go's own
    "printpass": {
        "go.mod": mod("printpass"),
        "printpass/p.go": "package printpass\n",
        "printpass/p_test.go": 'package printpass\n\nimport (\n\t"fmt"\n\t"os"\n\t"testing"\n)\n\nfunc TestMain(m *testing.M) {\n\tfmt.Println("PASS")\n\tos.Exit(0)\n}\n\nfunc TestBroken(t *testing.T) {\n\tt.Fatal("never runs")\n}\n',
        "ok/ok.go": "package ok\n",
        "ok/ok_test.go": "package ok\n\nimport \"testing\"\n\nfunc TestOK(t *testing.T) {\n}\n",
    },
    # services/ holds nested modules and one file go leaves out of the root module
    "nested_excluded": {
        "go.mod": mod("nested_excluded"),
        "root.go": "package nested_excluded\n",
        "services/gen.go": "//go:build ignore\n\npackage main\n\nfunc main() {}\n",
        "services/api/go.mod": "module example.com/nested_excluded/services/api\n\ngo 1.21\n",
        "services/api/api.go": "package api\n",
        "services/api/api_test.go": 'package api\n\nimport "testing"\n\nfunc TestAPI(t *testing.T) {\n}\n',
        "services/web/go.mod": "module example.com/nested_excluded/services/web\n\ngo 1.21\n",
        "services/web/web.go": "package web\n",
        "services/web/web_test.go": 'package web\n\nimport "testing"\n\nfunc TestWeb(t *testing.T) {\n}\n',
    },
    "grid": {
        "go.mod": mod("grid"),
        **SMALL,
        "calc_test.go": test_file(
            "func TestGrid(t *testing.T) {\n"
            '\tfor _, row := range []string{"x", "y"} {\n'
            "\t\tt.Run(row, func(t *testing.T) {\n"
            '\t\t\tfor _, col := range []string{"1", "2"} {\n'
            "\t\t\t\tt.Run(col, func(t *testing.T) {\n"
            '\t\t\t\t\tif (row == "x" && col == "2") || (row == "y" && col == "1") {\n'
            '\t\t\t\t\t\tt.Errorf("%s/%s was not asked for", row, col)\n'
            "\t\t\t\t\t}\n\t\t\t\t})\n\t\t\t}\n\t\t})\n\t}\n}\n",
        ),
    },
    "allskip": {
        "go.mod": mod("allskip"),
        **SMALL,
        "calc_test.go": test_file(
            'func TestSkipOne(t *testing.T) {\n\tt.Skip("no")\n}\n',
            'func TestSkipTwo(t *testing.T) {\n\tt.Skip("no")\n}\n',
        ),
    },
    # ExampleAdd has no Output comment: go compiles it and never runs it.
    "example_noout": {
        "go.mod": mod("example_noout"),
        **SMALL,
        "calc_test.go": test_file(
            PASSING,
            "func ExampleAdd() {\n\t_ = Add(1, 2)\n}\n",
            "func ExampleAdd_withOutput() {\n\t_ = Add(1, 2)\n\t// Output:\n}\n",
            "func ExampleAdd_unordered() {\n\t_ = Add(1, 2)\n\t// Unordered output:\n}\n",
        ),
    },
    # services/ holds only nested modules: the root module has no package below it.
    "nestedmods": {
        "go.mod": mod("nestedmods"),
        "root.go": "package nestedmods\n",
        "root_test.go": 'package nestedmods\n\nimport "testing"\n\nfunc TestRoot(t *testing.T) {\n}\n',
        "services/api/go.mod": "module example.com/nestedmods/services/api\n\ngo 1.21\n",
        "services/api/api.go": "package api\n",
        "services/api/api_test.go": 'package api\n\nimport "testing"\n\nfunc TestAPI(t *testing.T) {\n}\n',
        "services/web/go.mod": "module example.com/nestedmods/services/web\n\ngo 1.21\n",
        "services/web/web.go": "package web\n",
        "services/web/web_test.go": 'package web\n\nimport "testing"\n\nfunc TestWeb(t *testing.T) {\n}\n',
    },
    "multimod": {
        "go.mod": mod("multimod"),
        "root.go": "package multimod\n\nfunc Root() int { return 1 }\n",
        "root_test.go": 'package multimod\n\nimport "testing"\n\nfunc TestRoot(t *testing.T) {\n\tif Root() != 1 {\n\t\tt.Fatal("root")\n\t}\n}\n',
        "sub/go.mod": "module example.com/multimod/sub\n\ngo 1.21\n",
        "sub/sub.go": "package sub\n\nfunc Sub() int { return 2 }\n",
        "sub/sub_test.go": 'package sub\n\nimport "testing"\n\nfunc TestSub(t *testing.T) {\n\tif Sub() != 3 {\n\t\tt.Error("sub is wrong")\n\t}\n}\n',
    },
}

# ripple's own flags (go_check.BASE_ARGV): they win over GOFLAGS in every spelling, where it matters.
RUN = ["go", "test", "-json", "-count=1", "-skip=", "-list=", "-c=false", "-fuzz="]
# a plain go test, to show what GOFLAGS does to one
PLAIN = ["go", "test", "-json", "-count=1"]


def run_spec(name, module, cwd, extra, packages, request, by_hand=False, env=None, plain=False):
    """by_hand: the run asks for something ripple never builds (a pattern no id gave, a go flag), so
    no request maps to it and test_check_go_plan.py leaves it out. env: environment variables the run was made with.
    plain: a bare `go test -json -count=1`, none of ripple's overrides (what GOFLAGS does to it)."""
    if not plain and "-run" not in extra:
        extra = ["-run="] + extra  # a run with no pattern of its own says so, and so beats a -run in GOFLAGS
    return {
        "name": name, "module": module, "cwd": cwd, "argv": (PLAIN if plain else RUN) + extra + packages,
        "request": request, "by_hand": by_hand or plain, "env": env or {}, "plain": plain,
    }  # fmt: skip


RUNS = [
    run_spec("pass", "pass", ".", [], ["./..."], None),
    run_spec("fail", "fail", ".", [], ["./..."], None),
    run_spec("build_failure", "build_failure", ".", [], ["./..."], None),
    run_spec("test_build_failure", "test_build_failure", ".", [], ["./..."], None),
    run_spec("vet_failure", "vet_failure", ".", [], ["./..."], None),
    run_spec("no_test_files", "no_test_files", ".", [], ["./..."], None),
    run_spec("nomatch", "nomatch", ".", ["-run", "^(TestNope)$"], ["."], None, by_hand=True),
    run_spec("tagged", "tagged", ".", ["-run", "^(TestTagged)$"], ["."], ["tagged_test.go::TestTagged"]),
    run_spec("panic", "panic", ".", [], ["./..."], None),
    run_spec("timeout", "timeout", ".", ["-timeout", "1s"], ["./..."], None, by_hand=True),
    run_spec("subtests_all", "subtests", ".", [], ["./..."], None),
    run_spec("subtests_one", "subtests", ".", ["-run", "^TestTable$/^(bad)$"], ["."], ["calc_test.go::TestTable/bad"]),
    run_spec("subtests_slash", "subtests", ".", ["-run", "^TestTable$/^a$/^(b)$"], ["."], ["calc_test.go::TestTable/a/b"]),
    run_spec("subtests_space", "subtests", ".", ["-run", "^TestTable$/^(with_space)$"], ["."], ["calc_test.go::TestTable/with_space"]),
    run_spec("subtests_missing", "subtests", ".", ["-run", "^TestTable$/^(nope)$"], ["."], ["calc_test.go::TestTable/nope"]),
    run_spec("twopkgs_all", "twopkgs", ".", [], ["./..."], None),
    run_spec("twopkgs_a_only", "twopkgs", ".", ["-run", "^(TestSame)$"], ["./a"], ["a/a_test.go::TestSame"]),
    run_spec(
        "twopkgs_each", "twopkgs", ".", ["-run", "^(TestSame)$"], ["./a"], ["a/a_test.go::TestSame"]
    ),
    run_spec("twopkgs_b_only", "twopkgs", ".", ["-run", "^(TestSame)$"], ["./b"], ["b/b_test.go::TestSame"]),
    run_spec("skipped_all", "skipped", ".", [], ["./..."], None),
    run_spec("skipped_only", "skipped", ".", ["-run", "^(TestSkipped)$"], ["."], ["calc_test.go::TestSkipped"]),
    run_spec("exit_early", "exit_early", ".", [], ["./..."], None),
    run_spec("killed", "killed", ".", [], ["./..."], None),
    run_spec("testmain_noop", "testmain_noop", ".", [], ["./..."], None),
    run_spec("multimod_root", "multimod", ".", [], ["./..."], None),
    run_spec("multimod_sub", "multimod", "sub", [], ["./..."], None),
    # a test binary that exits 0 before running a test: with other packages in the run, and alone
    run_spec("silent_exit_all", "silent_exit", ".", [], ["./..."], None),
    run_spec("silent_exit_dir", "silent_exit", ".", [], ["./..."], ["."]),
    run_spec("silent_exit_one", "silent_exit", ".", [], ["./initexit/..."], ["initexit"]),
    # the grid: x/1 and y/2 asked for, x/2 and y/1 (which fail) not
    run_spec("grid_all", "grid", ".", [], ["./..."], None),
    run_spec("grid_x1", "grid", ".", ["-run", "^TestGrid$/^x$/^(1)$"], ["."], ["calc_test.go::TestGrid/x/1"]),
    run_spec("grid_y2", "grid", ".", ["-run", "^TestGrid$/^y$/^(2)$"], ["."], ["calc_test.go::TestGrid/y/2"]),
    # the cells that fail: asked for together they are two runs of one test, and one failure
    run_spec("grid_x2", "grid", ".", ["-run", "^TestGrid$/^x$/^(2)$"], ["."], ["calc_test.go::TestGrid/x/2"]),
    run_spec("grid_y1", "grid", ".", ["-run", "^TestGrid$/^y$/^(1)$"], ["."], ["calc_test.go::TestGrid/y/1"]),
    run_spec(
        "grid_cross_product", "grid", ".", ["-run", "^TestGrid$/^(x|y)$/^(1|2)$"], ["."], None, by_hand=True
    ),
    # a directory argument whose packages have no test to run
    run_spec("no_test_files_dir", "no_test_files", ".", [], ["./..."], ["."]),
    run_spec("allskip_dir", "allskip", ".", [], ["./..."], ["."]),
    # a whole file: ExampleAdd has no Output comment and is not asked for
    run_spec(
        "example_noout", "example_noout", ".", ["-run", "^(ExampleAdd_unordered|ExampleAdd_withOutput|TestAdd)$"], ["."], ["calc_test.go"]
    ),
    run_spec("example_noout_unrun", "example_noout", ".", ["-run", "^(ExampleAdd)$"], ["."], None, by_hand=True),
    # a directory of nested modules: one run in each, and what the root module made of ./services/...
    run_spec("nested_services_api", "nestedmods", "services/api", [], ["./..."], ["services"]),
    run_spec("nested_services_web", "nestedmods", "services/web", [], ["./..."], ["services"]),
    run_spec("nested_services_holder", "nestedmods", ".", [], ["./services/..."], None, by_hand=True),
    run_spec("quiet_all", "quiet", ".", [], ["./..."], None),
    run_spec("quiet_bench", "quiet", ".", [], ["./..."], None, env={"GOFLAGS": "-bench=. -benchtime=1x"}),
    run_spec("bench_ok", "bench_ok", ".", [], ["./..."], None, env={"GOFLAGS": "-bench=. -benchtime=1x"}),
    run_spec("bench_bad", "bench_bad", ".", [], ["./..."], None, env={"GOFLAGS": "-bench=. -benchtime=1x"}),
    run_spec("printpass_all", "printpass", ".", [], ["./..."], None),
    run_spec("nested_excluded_api", "nested_excluded", "services/api", [], ["./..."], ["services"]),
    run_spec("nested_excluded_web", "nested_excluded", "services/web", [], ["./..."], ["services"]),
    run_spec("nested_excluded_holder", "nested_excluded", ".", [], ["./services/..."], None, by_hand=True),
    # GOFLAGS from the environment reaches every go test
    run_spec("goflags_exec_true", "fail", ".", [], ["./..."], None, env={"GOFLAGS": "-exec=true"}),
    run_spec("goflags_run_plain", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-run=^TestAdd$'}, plain=True),
    run_spec("goflags_run", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-run=^TestAdd$'}),
    run_spec("goflags_test_run_plain", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-test.run=^TestAdd$'}, plain=True),
    run_spec("goflags_test_run", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-test.run=^TestAdd$'}),
    run_spec("goflags_quoted_run_plain", "fail", ".", [], ["./..."], None, env={"GOFLAGS": "'-run=^TestAdd$'"}, plain=True),
    run_spec("goflags_quoted_run", "fail", ".", [], ["./..."], None, env={"GOFLAGS": "'-run=^TestAdd$'"}),
    run_spec("goflags_test_skip_plain", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-test.skip=Bad'}, plain=True),
    run_spec("goflags_test_skip", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-test.skip=Bad'}),
    run_spec("goflags_list_plain", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-list=.'}, plain=True),
    run_spec("goflags_list", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-list=.'}),
    run_spec("goflags_c_plain", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-c'}, plain=True),
    run_spec("goflags_c", "fail", ".", [], ["./..."], None, env={"GOFLAGS": '-c'}),
]
# twopkgs_each is the same command as twopkgs_a_only and would only repeat its capture.
RUNS = [r for r in RUNS if r["name"] != "twopkgs_each"]


def write_modules() -> None:
    shutil.rmtree(MODULES, ignore_errors=True)
    for name, files in SOURCES.items():
        for rel, text in files.items():
            path = MODULES / name / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)


def go_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    # GOENV=off: the go env file of whoever captures is not read (an empty GOFLAGS in the environment would not stop it)
    env.update({"GOPROXY": "off", "GOTOOLCHAIN": "local", "GOFLAGS": "", "GOWORK": "off", "CGO_ENABLED": "0", "GOENV": "off"})
    env.update(extra or {})
    return env


def capture(go: str) -> None:
    shutil.rmtree(CAPTURES, ignore_errors=True)
    CAPTURES.mkdir()
    for spec in RUNS:
        root = WORK / spec["module"]
        shutil.rmtree(root, ignore_errors=True)
        shutil.copytree(MODULES / spec["module"], root)
        cwd = (root / spec["cwd"]).resolve()
        argv = [go] + spec["argv"][1:]
        packages = [a for a in spec["argv"][len(PLAIN if spec["plain"] else RUN):] if a.startswith(".")]
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, env=go_env(spec["env"]), timeout=300, text=True)
        listed = subprocess.run(
            [go, "list", "-e", "-f", "{{.ImportPath}}\t{{.Dir}}"] + packages,
            cwd=cwd, capture_output=True, env=go_env(), timeout=120, text=True,
        )
        directories = {}
        for line in listed.stdout.splitlines():
            path, _, directory = line.partition("\t")
            directories[path] = os.path.relpath(directory, root)
        # the one line that names the machine (a benchmark prints its cpu model) is not part of what a stream is read for
        stdout = re.sub(r"cpu: [^\\\"]*", "cpu: redacted", proc.stdout)
        (CAPTURES / f"{spec['name']}.jsonl").write_text(stdout)
        meta = {
            "module": spec["module"],
            "cwd": spec["cwd"],
            "argv": spec["argv"],
            "request": spec["request"],
            "by_hand": spec["by_hand"],
            "env": spec["env"],
            "exit_code": proc.returncode,
            "stderr": proc.stderr,
            "go_version": subprocess.run([go, "version"], capture_output=True, text=True).stdout.strip(),
            "directories": directories,
        }
        (CAPTURES / f"{spec['name']}.json").write_text(json.dumps(meta, indent=2) + "\n")
        print(spec["name"], proc.returncode, len(proc.stdout.splitlines()), "events")


if __name__ == "__main__":
    write_modules()
    capture(sys.argv[1] if len(sys.argv) > 1 else "go")
