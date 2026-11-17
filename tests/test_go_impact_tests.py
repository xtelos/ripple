"""`impact` on a Go symbol lists the Go tests that reach it, under ids `check` accepts and runs.

A Go test is a function in a _test.go file that `go test` runs: `TestXxx(t *testing.T)`, `FuzzXxx(f *testing.F)`
(its seed corpus runs as a test) and an `ExampleXxx()` that ends in an `// Output:` comment. The id is
`<repo-relative file>::<Name>`, the form `check` takes first. What the answer leaves out is said in
`tests_not_listed`, with the reason, never listed under an id `go test` would not run (a benchmark, a test in a
file only some builds have, a directory whose test build fails on a function ripple cannot read as a test).

The tests that read Go need tree-sitter-go and are skipped without it; the ones that run `go` are skipped without
the toolchain (offline, GOPROXY=off).
"""

import importlib.util
import json
import shutil
import subprocess

import pytest

from ripple import queries
from ripple.cache import load_graph, load_repo
from ripple.checker import run_check
from ripple.go_check import parse_wants
from ripple.indexer import build_graph
from ripple.model import Graph
from ripple.profile import GO, PYTHON

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
needs_go_parser = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")
needs_go_tool = pytest.mark.skipif(shutil.which("go") is None, reason="the go toolchain is not on PATH")

MOD = "module example.com/m\n\ngo 1.21\n"
LIB = "package m\n\nfunc Add(a, b int) int { return a + b }\n"
HEAD = 'package m\n\nimport "testing"\n\n'


def impact(repo, query="Add"):
    return queries.impact(build_graph(repo, GO), query)


def ids(result):
    return [t["node_id"] for t in result["tests"]]


def not_listed(result):
    return {entry["symbol"]: entry["reason"] for entry in result.get("tests_not_listed", [])}


@pytest.fixture(autouse=True)
def offline_go(monkeypatch):
    for key, value in {"GOPROXY": "off", "GOTOOLCHAIN": "local", "GOFLAGS": "", "GOWORK": "off", "CGO_ENABLED": "0", "GOENV": "off"}.items():
        monkeypatch.setenv(key, value)


# ---- the ids ----------------------------------------------------------------------------------------------


@needs_go_parser
def test_a_test_that_reaches_a_go_symbol_is_listed_under_the_file_and_name_check_takes(make_repo):
    repo = make_repo({"go.mod": MOD, "x.go": LIB, "x_test.go": HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n"})
    result = impact(repo)
    assert result["tests"] == [
        {"node_id": "x_test.go::TestAdd", "symbol": "example.com/m:TestAdd", "line": 5, "distance": 1, "file": "x_test.go"}
    ]
    assert result["test_count"] == 1
    assert result["by_file"] == {}
    assert result["caller_count"] == 0


@needs_go_parser
def test_a_helper_between_the_test_and_the_symbol_keeps_the_test_listed_and_the_helper_a_caller(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "helper.go": "package m\n\nfunc Twice(n int) int { return Add(n, n) }\n",
            "x_test.go": HEAD + "func TestTwice(t *testing.T) {\n    Twice(2)\n}\n\nfunc helper(t *testing.T) {\n    Add(1, 1)\n}\n",
        }
    )
    result = impact(repo)
    assert ids(result) == ["x_test.go::TestTwice"]
    assert result["tests"][0]["distance"] == 2
    assert sorted(e["symbol"] for e in result["by_file"]["x_test.go"]) == ["example.com/m:helper"]  # not a test: go never runs it alone
    assert [e["symbol"] for e in result["by_file"]["helper.go"]] == ["example.com/m:Twice"]


@needs_go_parser
def test_a_test_of_a_subdirectory_package_is_listed_by_its_own_path_and_a_same_named_test_elsewhere_is_another_id(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n",
            "sub/s.go": 'package sub\n\nimport "example.com/m"\n\nfunc S() int { return m.Add(1, 1) }\n',
            "sub/s_test.go": 'package sub\n\nimport "testing"\n\nfunc TestAdd(t *testing.T) {\n    S()\n}\n',
        }
    )
    assert sorted(ids(impact(repo))) == ["sub/s_test.go::TestAdd", "x_test.go::TestAdd"]


@needs_go_parser
def test_fuzz_targets_and_examples_with_an_output_comment_are_listed(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": HEAD
            + "func FuzzAdd(f *testing.F) {\n    f.Add(1)\n    f.Fuzz(func(t *testing.T, n int) { Add(n, n) })\n}\n\n"
            + "func ExampleAdd() {\n    Add(1, 2)\n    // Output:\n}\n",
        }
    )
    result = impact(repo)
    assert sorted(ids(result)) == ["x_test.go::ExampleAdd", "x_test.go::FuzzAdd"]
    assert result["test_count"] == 2


@needs_go_parser
def test_an_example_without_an_output_comment_is_compiled_and_never_run_so_it_has_no_id(make_repo):
    repo = make_repo({"go.mod": MOD, "x.go": LIB, "x_test.go": HEAD + "func ExampleAdd() {\n    Add(1, 2)\n}\n"})
    result = impact(repo)
    assert ids(result) == []
    assert "Output" in not_listed(result)["example.com/m:ExampleAdd"]


@needs_go_parser
def test_a_benchmark_is_not_listed_because_go_test_does_not_run_it_without_bench_and_check_does_not_take_it(make_repo):
    repo = make_repo({"go.mod": MOD, "x.go": LIB, "x_test.go": HEAD + "func BenchmarkAdd(b *testing.B) {\n    Add(1, 2)\n}\n"})
    result = impact(repo)
    assert ids(result) == []
    assert "benchmark" in not_listed(result)["example.com/m:BenchmarkAdd"]
    assert [e["symbol"] for e in result["by_file"]["x_test.go"]] == ["example.com/m:BenchmarkAdd"]  # still a caller, as before


@needs_go_parser
def test_a_subtest_is_not_collected_on_its_own_so_a_change_reached_through_one_lists_the_parent_test(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": HEAD
            + 'func TestTable(t *testing.T) {\n    t.Run("two", func(t *testing.T) {\n        Add(1, 1)\n    })\n}\n',
        }
    )
    result = impact(repo)
    assert ids(result) == ["x_test.go::TestTable"]  # the parent: go -run can address "TestTable/two", ripple names the test it can prove


# ---- what is not a test ------------------------------------------------------------------------------------


@needs_go_parser
def test_testmain_is_not_a_test(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": 'package m\n\nimport (\n    "os"\n    "testing"\n)\n\nfunc TestMain(m *testing.M) {\n    Add(0, 0)\n    os.Exit(m.Run())\n}\n\n'
            "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n",
        }
    )
    result = impact(repo)
    assert ids(result) == ["x_test.go::TestAdd"]
    assert [e["symbol"] for e in result["by_file"]["x_test.go"]] == ["example.com/m:TestMain"]


@needs_go_parser
def test_a_name_with_a_lowercase_letter_after_the_prefix_is_not_a_test_and_go_test_fails_the_package_so_no_id_is_given(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": HEAD + "func Testadd(t *testing.T) {\n    Add(1, 2)\n}\n\nfunc TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n",
        }
    )
    result = impact(repo)
    assert ids(result) == []
    reasons = not_listed(result)
    assert "Testadd" in reasons["example.com/m:TestAdd"]
    # Testadd is no test at all: it is a function of a test file that nothing listed calls, and is named as that
    assert "no listed test" in reasons["example.com/m:Testadd"]


@needs_go_parser
def test_a_function_with_a_test_name_and_the_wrong_signature_is_not_a_test_and_go_test_refuses_the_whole_package(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": HEAD + "func TestBad(n int) {\n    Add(n, n)\n}\n\nfunc TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n",
        }
    )
    result = impact(repo)
    assert ids(result) == []
    reasons = not_listed(result)
    assert set(reasons) == {"example.com/m:TestAdd", "example.com/m:TestBad"}  # TestBad is no test: a function nothing listed calls
    assert "TestBad" in reasons["example.com/m:TestAdd"]


@needs_go_parser
def test_a_signature_that_is_not_provably_testing_t_is_not_a_test(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": 'package m\n\nimport "testing"\n\nvar _ = testing.Short\n\ntype T struct{}\n\nfunc TestLocal(t *T) {\n    Add(1, 2)\n}\n',
        }
    )
    assert ids(impact(repo)) == []


@needs_go_parser
def test_an_import_alias_or_dot_import_of_testing_is_still_testing(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "a_test.go": 'package m\n\nimport tt "testing"\n\nfunc TestAliased(t *tt.T) {\n    Add(1, 2)\n}\n',
            "b_test.go": 'package m\n\nimport . "testing"\n\nfunc TestDotted(t *T) {\n    Add(1, 2)\n}\n',
        }
    )
    assert sorted(ids(impact(repo))) == ["a_test.go::TestAliased", "b_test.go::TestDotted"]


@needs_go_parser
def test_a_method_or_a_non_test_file_function_named_like_a_test_is_not_a_test(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": 'package m\n\nimport "testing"\n\nfunc Add(a, b int) int { return a + b }\n\nfunc TestNotInATestFile(t *testing.T) {\n    Add(1, 2)\n}\n',
            "x_test.go": HEAD + "type S struct{}\n\nfunc (S) TestMethod(t *testing.T) {\n    Add(1, 2)\n}\n",
        }
    )
    assert ids(impact(repo)) == []


# ---- the package and the build -----------------------------------------------------------------------------


@needs_go_parser
def test_a_test_of_an_external_test_package_is_listed_by_its_file(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": 'package m_test\n\nimport (\n    "testing"\n\n    "example.com/m"\n)\n\nfunc TestExt(t *testing.T) {\n    m.Add(1, 2)\n}\n',
        }
    )
    result = impact(repo)
    assert ids(result) == ["x_test.go::TestExt"]
    assert result["tests"][0]["symbol"] == "example.com/m_test:TestExt"


@needs_go_parser
def test_a_name_both_the_package_and_its_external_test_package_declare_runs_both_so_neither_is_given_an_id(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "a_test.go": HEAD + "func TestBoth(t *testing.T) {\n    Add(1, 2)\n}\n",
            "b_test.go": 'package m_test\n\nimport (\n    "testing"\n\n    "example.com/m"\n)\n\nfunc TestBoth(t *testing.T) {\n    m.Add(1, 2)\n}\n',
        }
    )
    result = impact(repo)
    assert ids(result) == []
    assert set(not_listed(result)) == {"example.com/m:TestBoth", "example.com/m_test:TestBoth"}


@needs_go_parser
@pytest.mark.parametrize(
    "name, head",
    [
        ("x_integration_test.go", "//go:build integration\n\n"),
        ("x_linux_test.go", ""),
        ("x_arm64_test.go", ""),
        ("x_old_test.go", "// +build ignore2\n\n"),
        ("x_go_test.go", "//go:build go1.18\n\n"),
    ],
)
def test_a_test_in_a_file_only_some_builds_include_is_not_given_an_id(make_repo, name, head):
    repo = make_repo({"go.mod": MOD, "x.go": LIB, name: head + HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n"})
    result = impact(repo)
    assert ids(result) == []
    assert "build" in not_listed(result)["example.com/m:TestAdd"]
    assert [e["symbol"] for e in result["by_file"][name]] == ["example.com/m:TestAdd"]


@needs_go_parser
def test_an_unconstrained_test_file_beside_a_constrained_one_keeps_its_id(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "a_test.go": HEAD + "func TestPlain(t *testing.T) {\n    Add(1, 2)\n}\n",
            "b_test.go": "//go:build integration\n\n" + HEAD + "func TestSlow(t *testing.T) {\n    Add(1, 2)\n}\n",
        }
    )
    result = impact(repo)
    assert ids(result) == ["a_test.go::TestPlain"]
    assert set(not_listed(result)) == {"example.com/m:TestSlow"}


@needs_go_parser
def test_a_test_with_no_go_mod_above_it_is_not_given_an_id_because_check_cannot_say_where_to_run_go_test(make_repo):
    repo = make_repo({"x.go": LIB, "x_test.go": HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n"})
    result = impact(repo, "Add")
    assert ids(result) == []
    assert "go.mod" in not_listed(result)["m:TestAdd"]


@needs_go_parser
def test_a_directory_with_a_file_ripple_could_not_read_completely_is_given_no_ids(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "broken.go": "package m\n\nfunc Broken( {{{\n",
            "x_test.go": HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n",
        }
    )
    result = impact(repo)
    assert ids(result) == []
    assert "broken.go" in not_listed(result)["example.com/m:TestAdd"]


@needs_go_parser
def test_a_test_file_that_imports_c_is_given_no_ids_because_go_test_refuses_cgo_in_test_files(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": 'package m\n\n// int one() { return 1; }\nimport "C"\nimport "testing"\n\nfunc TestAdd(t *testing.T) {\n    Add(int(C.one()), 2)\n}\n',
        }
    )
    assert ids(impact(repo)) == []


@needs_go_parser
def test_a_test_declared_in_a_way_check_does_not_read_is_given_no_id(make_repo):
    """check finds a test by `func Name(` at the start of a line: an id it would refuse is never handed out."""
    repo = make_repo({"go.mod": MOD, "x.go": LIB, "x_test.go": HEAD + "func  TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n"})
    result = impact(repo)
    assert ids(result) == []
    assert "check finds a test" in not_listed(result)["example.com/m:TestAdd"]


@needs_go_parser
def test_a_test_below_testdata_or_an_underscore_directory_is_not_read_so_it_is_not_listed(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "testdata/t_test.go": HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n",
            "_skip/t_test.go": HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n",
        }
    )
    result = impact(repo)
    assert ids(result) == [] and result["by_file"] == {}
    assert "tests_not_listed" not in result


@needs_go_parser
def test_a_test_in_a_nested_module_is_listed_by_its_path_from_the_repo_root(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "sub/go.mod": "module example.com/sub\n\ngo 1.21\n",
            "sub/s.go": "package sub\n\nfunc Add(a, b int) int { return a + b }\n",
            "sub/s_test.go": HEAD.replace("package m", "package sub") + "func TestSub(t *testing.T) {\n    Add(1, 2)\n}\n",
        }
    )
    assert ids(impact(repo, "example.com/sub:Add")) == ["sub/s_test.go::TestSub"]


# ---- what the answer is made of ----------------------------------------------------------------------------


@needs_go_parser
def test_the_ids_survive_the_cache_and_a_graph_with_none_writes_no_key(make_repo, tmp_path_factory):
    repo = make_repo({"go.mod": MOD, "x.go": LIB, "x_test.go": HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n"})
    cache = tmp_path_factory.mktemp("cache")
    first, cached = load_graph(repo, cache, profile=GO)
    again, cached_again = load_graph(repo, cache, profile=GO)
    assert (cached, cached_again) == (False, True)
    assert ids(queries.impact(again, "Add", profile=GO)) == ["x_test.go::TestAdd"]
    assert Graph.from_dict(json.loads(json.dumps(first.to_dict()))).test_ids == first.test_ids
    for language, files in ((GO, {"go.mod": MOD, "x.go": LIB}), (PYTHON, {"test_a.py": "def test_a():\n    assert True\n"})):
        plain = tmp_path_factory.mktemp("plain")
        for name, source in files.items():
            (plain / name).write_text(source)
        written = build_graph(plain, language).to_dict()
        assert "test_ids" not in written and "tests_withheld" not in written  # a graph with none is written as it was


@needs_go_parser
def test_the_ids_survive_the_merge_of_a_repo_of_two_languages(make_repo, tmp_path_factory):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "x_test.go": HEAD + "func TestAdd(t *testing.T) {\n    Add(1, 2)\n}\n",
            "tool.py": "def helper():\n    return 1\n",
            "test_tool.py": "from tool import helper\n\n\ndef test_helper():\n    helper()\n",
        }
    )
    graph = load_repo(repo, tmp_path_factory.mktemp("cache")).graph
    assert ids(queries.impact(graph, "example.com/m:Add")) == ["x_test.go::TestAdd"]
    assert ids(queries.impact(graph, "tool.helper")) == ["test_tool.py::test_helper"]


# ---- the real toolchain ------------------------------------------------------------------------------------


ROUND_TRIP = {
    "go.mod": MOD,
    "x.go": LIB,
    "y.go": "package m\n\nfunc Sub(a, b int) int { return a - b }\n",
    "x_test.go": HEAD
    + "func TestAdd(t *testing.T) {\n    if Add(1, 2) != 3 {\n        t.Fatal(\"add\")\n    }\n}\n\n"
    + "func TestAddAgain(t *testing.T) {\n    Add(2, 2)\n}\n\n"
    + "func FuzzAdd(f *testing.F) {\n    f.Add(1)\n    f.Fuzz(func(t *testing.T, n int) { Add(n, n) })\n}\n\n"
    + "func ExampleAdd() {\n    Add(1, 2)\n    // Output:\n}\n\n"
    + 'func TestSubFails(t *testing.T) {\n    Sub(1, 2)\n    t.Fatal("this test does not reach Add and must not run")\n}\n',
    "sub/s.go": 'package sub\n\nimport "example.com/m"\n\nfunc Three() int { return m.Add(1, 2) }\n',
    "sub/s_test.go": 'package sub_test\n\nimport (\n    "testing"\n\n    "example.com/m/sub"\n)\n\nfunc TestThree(t *testing.T) {\n    sub.Three()\n}\n',
}


@needs_go_parser
@needs_go_tool
def test_the_ids_impact_gives_go_into_check_and_exactly_those_tests_run(make_repo):
    repo = make_repo(ROUND_TRIP)
    asked = ids(impact(repo))
    assert sorted(asked) == ["sub/s_test.go::TestThree", "x_test.go::ExampleAdd", "x_test.go::FuzzAdd", "x_test.go::TestAdd", "x_test.go::TestAddAgain"]
    result = run_check(repo, tests=asked, profile=GO)
    assert result["passed"] is True, result
    assert result["counts"]["passed"] == 5 and result["counts"]["failed"] == 0  # TestSubFails would have failed had it run
    assert result["counts"]["packages"] == 2
    assert result["summary"].startswith("5 passed, 0 failed")


@needs_go_parser
@needs_go_tool
def test_one_id_runs_only_that_test(make_repo):
    repo = make_repo(ROUND_TRIP)
    result = run_check(repo, tests=["x_test.go::TestAdd"], profile=GO)
    assert result["passed"] is True and result["counts"]["passed"] == 1


# go test -list is the oracle for what a test is. Each case is the body of one _test.go file of package m (the
# library has Add); `listed` is what `go test -list` names, benchmarks left out (check does not take them), and
# None when go refuses the package, where ripple must name no test either.
CASES = {
    "plain": ('import "testing"\nfunc TestA(t *testing.T) { Add(1, 1) }', ["TestA"]),
    "unnamed parameter": ('import "testing"\nfunc TestA(*testing.T) { Add(1, 1) }', ["TestA"]),
    "blank parameter": ('import "testing"\nfunc TestA(_ *testing.T) { Add(1, 1) }', ["TestA"]),
    "underscore and digit": ('import "testing"\nfunc Test_a(t *testing.T) { Add(1, 1) }\nfunc Test1(t *testing.T) { Add(1, 1) }', ["Test_a", "Test1"]),
    "bare Test": ('import "testing"\nfunc Test(t *testing.T) { Add(1, 1) }', ["Test"]),
    "unicode upper": ('import "testing"\nfunc TestÉ(t *testing.T) { Add(1, 1) }', ["TestÉ"]),
    "unicode other": ('import "testing"\nfunc Test日本(t *testing.T) { Add(1, 1) }', ["Test日本"]),
    # Go's lowercase is the category Ll. Python's str.islower() is also true for Other_Lowercase letters (Lm and Lo), which
    # go takes as a name: these are tests go runs, and check must take their ids
    "modifier letter": ('import "testing"\nfunc Testʰ(t *testing.T) { Add(1, 1) }', ["Testʰ"]),
    "ordinal indicator": ('import "testing"\nfunc Testª(t *testing.T) { Add(1, 1) }', ["Testª"]),
    "phonetic capital": ('import "testing"\nfunc Testᴬ(t *testing.T) { Add(1, 1) }', ["Testᴬ"]),
    "titlecase letter": ('import "testing"\nfunc Testǅ(t *testing.T) { Add(1, 1) }', ["Testǅ"]),
    "script small l": ('import "testing"\nfunc Testℓ(t *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "TestMain": ('import (\n"os"\n"testing"\n)\nfunc TestMain(m *testing.M) { os.Exit(m.Run()) }\nfunc TestA(t *testing.T) { Add(1, 1) }', ["TestA"]),
    "TestMain taking T": ('import "testing"\nfunc TestMain(t *testing.T) { Add(1, 1) }', ["TestMain"]),
    "BenchmarkMain": ('import "testing"\nfunc BenchmarkMain(b *testing.B) { Add(1, 1) }', []),
    "lowercase after Test": ('import "testing"\nfunc Testa(t *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "lowercase unicode": ('import "testing"\nfunc Testé(t *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "wrong parameter type": ('import "testing"\nfunc TestA(t *testing.B) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "no parameter": ('import "testing"\nvar _ = testing.Short\nfunc TestA() { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "two parameters": ('import "testing"\nfunc TestA(t *testing.T, n int) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "two names": ('import "testing"\nfunc TestA(t, u *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "value parameter": ('import "testing"\nfunc TestA(t testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "variadic": ('import "testing"\nfunc TestA(t ...*testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "result": ('import "testing"\nfunc TestA(t *testing.T) int { return Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "type parameters": ('import "testing"\nfunc TestA[T any](t *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "method": ('import "testing"\ntype S struct{}\nfunc (S) TestA(t *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', ["TestB"]),
    "variable": ('import "testing"\nvar TestA = func(t *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', ["TestB"]),
    "fuzz": ('import "testing"\nfunc FuzzA(f *testing.F) { f.Add(1); f.Fuzz(func(t *testing.T, n int) { Add(n, 1) }) }', ["FuzzA"]),
    "fuzz without a seed": ('import "testing"\nfunc FuzzA(f *testing.F) { f.Fuzz(func(t *testing.T, n int) { Add(n, 1) }) }', ["FuzzA"]),
    "fuzz taking T": ('import "testing"\nfunc FuzzA(t *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "lowercase after Fuzz": ('import "testing"\nfunc Fuzza(f *testing.F) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "benchmark": ('import "testing"\nfunc BenchmarkA(b *testing.B) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', ["TestB"]),
    "benchmark taking T": ('import "testing"\nfunc BenchmarkA(t *testing.T) { Add(1, 1) }\nfunc TestB(t *testing.T) { Add(1, 1) }', None),
    "example with output": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// Output:\n}', ["ExampleAdd"]),
    "example with a suffix": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd_second() {\n// Output:\n}', ["ExampleAdd_second"]),
    "package example": ('import "testing"\nvar _ = testing.Short\nfunc Example() {\n// Output:\n}', ["Example"]),
    "unordered output": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// Unordered output: 1\n}', ["ExampleAdd"]),
    "output in any case": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// output: 1\n}', ["ExampleAdd"]),
    "example without output": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() { Add(1, 1) }', []),
    "output that is not the last comment": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// Output:\n_ = 1\n// done\n}', None),
    "output in a block comment": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n/* Output: */\n}', ["ExampleAdd"]),
    "output after other lines of its comment": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// shows Add.\n// Output:\n}', []),
    "output nested in a block": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\nif true {\n// Output:\n}\n}', ["ExampleAdd"]),
    "output after a blank line": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// shows Add.\n\n// Output:\n}', ["ExampleAdd"]),
    "output before a blank line": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// Output:\n\n// shows Add.\n}', None),
    "two outputs": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// Output:\n_ = 1\n// Output:\n}', None),
    "output trailing code": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n_ = 1 // Output:\n}', ["ExampleAdd"]),
    "trailing comment after output": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// Output:\n_ = 1 // done\n}', None),
    "two examples": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() {\n// Output:\n}\nfunc ExampleAdd_two() { Add(1, 1) }', ["ExampleAdd"]),
    "example taking a parameter": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd(n int) {\n// Output:\n}', None),
    "example with a result": ('import "testing"\nvar _ = testing.Short\nfunc ExampleAdd() int {\n// Output:\nreturn 1\n}', None),
    "lowercase after Example": ('import "testing"\nvar _ = testing.Short\nfunc Exampleadd() {\n// Output:\n}', None),
}


WITHHELD_THOUGH_GO_LISTS_THEM = ("output in a block comment", "output trailing code", "fuzz without a seed")


def go_list(directory):
    """(go's exit status, the names `go test -list` prints) for the package in directory, with go's default vet."""
    done = subprocess.run(["go", "test", "-list", ".*", "."], cwd=directory, capture_output=True, text=True)
    names = [line for line in done.stdout.splitlines() if line and not line.startswith(("ok", "FAIL", "?"))]
    return done.returncode, names


@needs_go_parser
@needs_go_tool
@pytest.mark.parametrize("case", sorted(CASES))
def test_what_ripple_collects_is_what_go_test_lists(make_repo, case):
    body, expected = CASES[case]
    repo = make_repo({"go.mod": MOD, "x.go": LIB, "x_test.go": "package m\n\n" + body + "\n"})
    status, listed = go_list(repo)
    if expected is None:
        assert status != 0, listed  # go refuses the package: no test of it runs, so ripple names none
    else:
        assert status == 0, listed
        assert sorted(n for n in listed if not n.startswith("Benchmark")) == sorted(expected)  # the table says what go says
    given = list(build_graph(repo, GO).test_ids.values())
    mine = sorted(name.partition("::")[2] for name in given)
    # go lists an example whose output is in a /* */ comment; check's reading of a file takes only `//` comments and
    # would refuse the id, so ripple does not hand it out
    # (the same for an output comment that follows code on its line: go lists it, check's line pattern does not match it)
    # and a fuzz target with no seed is listed by go and runs nothing under plain `go test`
    assert mine == ([] if case in WITHHELD_THOUGH_GO_LISTS_THEM else sorted(expected or []))
    if given:  # every id handed out is one check takes, for the very test it names
        assert [want.name for want in parse_wants(repo, given)] == [id.partition("::")[2] for id in given]


@needs_go_parser
@needs_go_tool
def test_ids_in_a_package_with_an_external_test_package_and_a_tagged_file_are_ones_go_lists(make_repo):
    repo = make_repo(
        {
            "go.mod": MOD,
            "x.go": LIB,
            "a_test.go": HEAD + "func TestIn(t *testing.T) { Add(1, 1) }\n",
            "b_test.go": 'package m_test\n\nimport (\n"testing"\n"example.com/m"\n)\nfunc TestOut(t *testing.T) { m.Add(1, 1) }\n',
            "c_test.go": "//go:build integration\n\n" + HEAD + "func TestTagged(t *testing.T) { Add(1, 1) }\n",
            "d_windows_test.go": HEAD + "func TestWindows(t *testing.T) { Add(1, 1) }\n",
        }
    )
    status, listed = go_list(repo)
    assert status == 0 and sorted(listed) == ["TestIn", "TestOut"]
    graph = build_graph(repo, GO)
    assert sorted(graph.test_ids.values()) == ["a_test.go::TestIn", "b_test.go::TestOut"]
    assert sorted(graph.tests_withheld) == ["example.com/m:TestTagged", "example.com/m:TestWindows"]


# ---- the invariant: nothing that runs in a test binary is left out without a word ---------------------------------
#
# Every caller `impact` reaches that runs as part of a test binary without being called by a listed test is named in
# `tests_not_listed`: TestMain, init and package variables of a _test.go file (they run before every test of the
# directory), init and package variables of any file (they run in every binary that links the package), a method
# named like a test (a suite runs it by reflection), and any other symbol of a _test.go file that no listed test is
# known to call. The module is a reviewer's: each directory is one way a test reaches lib.Target and is not a listed id.

M1 = {
    "go.mod": "module example.com/m1\n\ngo 1.21\n",
    "lib/lib.go": "package lib\n\nfunc Target(x int) int { return x + 1 }\n\nfunc Other() int { return 0 }\n",
    "lib/main_test.go": 'package lib\n\nimport (\n    "os"\n    "testing"\n)\n\nfunc TestMain(m *testing.M) {\n    if Target(1) != 2 {\n        panic("setup broke")\n    }\n    os.Exit(m.Run())\n}\n',
    "lib/plain_test.go": 'package lib\n\nimport "testing"\n\nfunc TestPlain(t *testing.T) {\n    if Other() != 0 {\n        t.Fatal("x")\n    }\n}\n',
    "initf/initf.go": "package initf\n",
    "initf/initf_test.go": 'package initf_test\n\nimport (\n    "testing"\n\n    "example.com/m1/lib"\n)\n\nvar ok bool\n\nfunc init() {\n    ok = lib.Target(1) == 2\n}\n\nfunc TestF(t *testing.T) {\n    if !ok {\n        t.Fatal("f")\n    }\n}\n',
    "initp/initp.go": "package initp\n",
    "initp/initp_test.go": 'package initp\n\nimport (\n    "testing"\n\n    "example.com/m1/lib"\n)\n\nvar v = lib.Target(1)\n\nfunc TestI(t *testing.T) {\n    if v != 2 {\n        t.Fatal("i")\n    }\n}\n',
    "suite/suite.go": "package suite\n",
    "suite/suite_test.go": 'package suite\n\nimport (\n    "reflect"\n    "testing"\n\n    "example.com/m1/lib"\n)\n\ntype S struct{ t *testing.T }\n\nfunc (s *S) TestX() {\n    if lib.Target(1) != 2 {\n        s.t.Fatal("suite broke")\n    }\n}\n\nfunc TestSuite(t *testing.T) {\n    v := reflect.ValueOf(&S{t})\n    for i := 0; i < v.NumMethod(); i++ {\n        v.Method(i).Call(nil)\n    }\n}\n',
    "fz/fz_test.go": 'package fz\n\nimport (\n    "testing"\n\n    "example.com/m1/lib"\n)\n\nfunc FuzzNoSeed(f *testing.F) {\n    f.Fuzz(func(t *testing.T, x int) {\n        if lib.Target(x) != x+1 {\n            t.Fatal("fuzz broke")\n        }\n    })\n}\n',
    "dup/a_test.go": 'package dup\n\nimport (\n    "testing"\n\n    "example.com/m1/lib"\n)\n\nfunc TestD(t *testing.T) {\n    if lib.Target(1) != 2 {\n        t.Fatal("d")\n    }\n}\n',
    "dup/b_test.go": '//go:build !windows\n\npackage dup_test\n\nimport "testing"\n\nfunc TestD(t *testing.T) {\n    t.Log("the other TestD ran")\n}\n',
    "uni/uni_test.go": 'package uni\n\nimport (\n    "testing"\n\n    "example.com/m1/lib"\n)\n\nfunc Testʰ(t *testing.T) {\n    if lib.Target(1) != 2 {\n        t.Fatal("u")\n    }\n}\n',
    "dup/dup.go": "package dup\n",
    "uni/uni.go": "package uni\n",
    "fz/fz.go": "package fz\n",
}

# the same module with the symbol broken: go fails every package below that has a test reaching it
M1_BROKEN = {**M1, "lib/lib.go": "package lib\n\nfunc Target(x int) int { return x + 2 }\n\nfunc Other() int { return 0 }\n"}

# ways of reaching a function from code that is not in a test file, and from helpers of one
OWN = {
    "go.mod": "module example.com/own\n\ngo 1.21\n",
    "core/core.go": "package core\n\nfunc Target(x int) int { return x + 1 }\n",
    "core/init.go": "package core\n\nvar cache = Target(2)\n\nfunc init() {\n    Target(3)\n}\n",
    "core/core_test.go": 'package core\n\nimport "testing"\n\nfunc TestTarget(t *testing.T) {\n    if Target(1) != 2 {\n        t.Fatal("t")\n    }\n}\n\nfunc used(t *testing.T) {\n    Target(4)\n}\n\nfunc TestUses(t *testing.T) {\n    used(t)\n}\n\nfunc orphan(t *testing.T) {\n    Target(5)\n}\n\nfunc chained(t *testing.T) {\n    orphan(t)\n}\n',
}


def accounted_for(graph, result):
    """The reached symbols that run in a test binary and that neither `tests`, `tests_not_listed` nor a call path up to one of
    them accounts for, and the symbols of init or a package variable that `tests_not_listed` does not name.

    Written from the graph and the answer alone, apart from the code that makes the entries: a symbol is in a test binary when
    it is declared in a _test.go file or has a call in one that reaches the symbol asked about or another reached symbol.
    """
    tests = {t["symbol"] for t in result["tests"]}
    named = {e["symbol"] for e in result.get("tests_not_listed", [])}
    reached = {e["symbol"] for entries in result["by_file"].values() for e in entries} | tests | named
    around = reached | {result["symbol"]}

    def in_a_test_file(sid):
        return graph.symbols[sid].file.endswith("_test.go") or any(
            e.file.endswith("_test.go") and e.callee in around for e in graph.callees_index.get(sid, ())
        )

    def reaches_one(sid):  # up through its reached callers to a listed or named symbol
        seen, stack = {sid}, [sid]
        while stack:
            for edge in graph.callers_index.get(stack.pop(), ()):
                if edge.caller in tests | named:
                    return True
                if edge.caller in reached and edge.caller not in seen:
                    seen.add(edge.caller)
                    stack.append(edge.caller)
        return False

    leftovers = sorted(sid for sid in reached - tests - named if in_a_test_file(sid) and not reaches_one(sid))
    initialisers = sorted(
        sid
        for sid in reached - tests - named
        if graph.symbols[sid].kind == "module" or graph.symbols[sid].name == "init"
    )
    return leftovers + initialisers


@needs_go_parser
@pytest.mark.parametrize("files, symbol", [(M1, "example.com/m1/lib:Target"), (OWN, "example.com/own/core:Target")], ids=["reviewer", "own"])
def test_nothing_that_runs_in_a_test_binary_is_left_out_without_a_word(make_repo, files, symbol):
    repo = make_repo(files)
    graph = build_graph(repo, GO)
    result = queries.impact(graph, symbol)
    assert accounted_for(graph, result) == []


@needs_go_parser
def test_testmain_init_and_a_package_variable_of_a_test_file_name_the_package_whose_every_test_runs_them(make_repo):
    result = impact(make_repo(M1), "example.com/m1/lib:Target")
    named = {e["symbol"]: e for e in result["tests_not_listed"]}
    main = named["example.com/m1/lib:TestMain"]
    assert main["file"] == "lib/main_test.go" and main["line"] == 8
    assert "every test" in main["reason"] and "lib" in main["reason"] and "TestMain" in main["reason"]
    init = named["example.com/m1/initf_test:init"]
    assert init["file"] == "initf/initf_test.go" and "every test" in init["reason"]
    # a package variable's initializer is the package's own, whose symbol sits in the package's first file: the entry says where the call is
    variable = named["example.com/m1/initp"]
    assert variable["file"] == "initp/initp_test.go" and variable["line"] == 9
    assert "every test" in variable["reason"] and "initp" in variable["reason"]


@needs_go_parser
def test_a_method_named_like_a_test_is_named_as_run_by_a_suite_runner(make_repo):
    result = impact(make_repo(M1), "example.com/m1/lib:Target")
    entry = {e["symbol"]: e for e in result["tests_not_listed"]}["example.com/m1/suite:S.TestX"]
    assert entry["file"] == "suite/suite_test.go"
    assert "reflection" in entry["reason"] and "suite" in entry["reason"]


@needs_go_parser
def test_init_and_package_variables_of_a_non_test_file_say_every_binary_that_links_the_package_runs_them(make_repo):
    result = impact(make_repo(OWN), "example.com/own/core:Target")
    named = {e["symbol"]: e for e in result["tests_not_listed"]}
    assert set(named) >= {"example.com/own/core:init", "example.com/own/core"}
    for entry in (named["example.com/own/core:init"], named["example.com/own/core"]):
        assert entry["file"] == "core/init.go"
        assert "initialised" in entry["reason"] and "imports" in entry["reason"]
    assert "core/core_test.go::TestTarget" in ids(result)


MIXED_VARIABLES = {
    "go.mod": "module example.com/m3\n\ngo 1.22\n",
    "lib/lib.go": "package lib\n\nfunc Target(x int) int { return x + 1 }\n",
    "mixv2/mixv2.go": 'package mixv2\n\nimport "example.com/m3/lib"\n\nvar X = lib.Target(1)\n',
    "mixv2/mixv2_test.go": (
        'package mixv2\n\nimport (\n\t"testing"\n\n\t"example.com/m3/lib"\n)\n\nvar y = lib.Target(5)\n\n'
        "func TestMixv2(t *testing.T) { _ = y }\n"
    ),
    "user2/user2.go": 'package user2\n\nimport "example.com/m3/mixv2"\n\nfunc U() int { return mixv2.X }\n',
    "user2/user2_test.go": (
        'package user2\n\nimport "testing"\n\nfunc TestU2(t *testing.T) {\n\tif U() != 2 {\n\t\tt.Fatal("user2 sees a broken X")\n\t}\n}\n'
    ),
}


@needs_go_parser
def test_a_package_with_variables_in_a_test_file_and_in_a_library_file_names_the_importers_tests_too(make_repo):
    # go test fails user2's TestU2 when Target breaks (it reads mixv2.X), and passing the directory mixv2 to check runs
    # only mixv2's tests: the entry must be the one that speaks of every package that imports it
    result = impact(make_repo(MIXED_VARIABLES), "example.com/m3/lib:Target")
    entry = {e["symbol"]: e for e in result["tests_not_listed"]}["example.com/m3/mixv2"]
    assert entry["file"] == "mixv2/mixv2.go" and entry["line"] == 5
    assert "imports" in entry["reason"] and "pass the directory" not in entry["reason"]


@needs_go_parser
def test_a_helper_of_a_test_file_is_named_unless_a_listed_test_calls_it(make_repo):
    result = impact(make_repo(OWN), "example.com/own/core:Target")
    named = {e["symbol"] for e in result["tests_not_listed"]}
    assert "example.com/own/core:used" not in named  # TestUses calls it: the test is listed
    assert "example.com/own/core:TestUses" in {t["symbol"] for t in result["tests"]}
    assert {"example.com/own/core:orphan", "example.com/own/core:chained"} <= named  # nothing collected calls them
    reason = {e["symbol"]: e["reason"] for e in result["tests_not_listed"]}["example.com/own/core:orphan"]
    assert "no listed test" in reason


@needs_go_parser
def test_a_helper_called_only_beyond_the_depth_asked_is_named_not_dropped(make_repo):
    repo = make_repo(OWN)
    graph = build_graph(repo, GO)
    result = queries.impact(graph, "example.com/own/core:Target", depth=1)
    assert "example.com/own/core:used" in {e["symbol"] for e in result["tests_not_listed"]}  # TestUses is one hop further


@needs_go_parser
@needs_go_tool
def test_naming_a_package_whose_every_test_runs_the_symbol_is_true_check_on_its_directory_fails_when_it_breaks(make_repo):
    """go confirms the three shapes: with lib.Target broken, TestMain's panic fails lib (TestPlain never runs), the init fails
    initf (TestF) and the variable fails initp (TestI), and check takes the directory the entry names."""
    repo = make_repo(M1_BROKEN)
    for directory, failing in (("lib", "TestMain"), ("initf", "TestF"), ("initp", "TestI")):
        result = run_check(repo, tests=[directory], profile=GO)
        assert result["passed"] is False, (directory, result)
    assert "TestPlain" not in "".join(run_check(repo, tests=["lib"], profile=GO)["failures"])  # it never ran: the package failed first


@needs_go_parser
@needs_go_tool
def test_the_suite_method_is_run_by_a_test_go_does_run_and_fails_when_the_symbol_breaks(make_repo):
    repo = make_repo(M1_BROKEN)
    result = run_check(repo, tests=["suite/suite_test.go::TestSuite"], profile=GO)
    assert result["passed"] is False
    assert any("TestSuite" in line for line in result["failures"])


# ---- a fuzz target with no seed runs nothing ---------------------------------------------------------------------


def fuzz_repo(make_repo, body, target=LIB):
    return make_repo({"go.mod": MOD, "x.go": target, "fz_test.go": HEAD + body})


SEEDED = "func FuzzAdd(f *testing.F) {\n    f.Add(1)\n    f.Fuzz(func(t *testing.T, n int) {\n        if Add(n, 1) != n+1 {\n            t.Fatal(\"broken\")\n        }\n    })\n}\n"
BROKEN_LIB = "package m\n\nfunc Add(a, b int) int { return a + b + 1 }\n"


@needs_go_parser
def test_a_fuzz_target_is_listed_only_when_the_source_proves_it_has_a_seed(make_repo):
    result = impact(fuzz_repo(make_repo, SEEDED))
    assert ids(result) == ["fz_test.go::FuzzAdd"]


@needs_go_parser
@pytest.mark.parametrize(
    "body",
    [
        "func FuzzAdd(f *testing.F) {\n    f.Fuzz(func(t *testing.T, n int) { Add(n, 1) })\n}\n",
        "func FuzzAdd(f *testing.F) {\n    f.Fuzz(func(t *testing.T, n int) { Add(n, 1) })\n    f.Add(1)\n}\n",  # a seed added after the fuzz call is not one
        "func FuzzAdd(f *testing.F) {\n    for _, n := range []int{1} {\n        f.Add(n)\n    }\n    f.Fuzz(func(t *testing.T, n int) { Add(n, 1) })\n}\n",  # a loop may add none
        "func FuzzAdd(f *testing.F) {\n    if testing.Short() {\n        f.Add(1)\n    }\n    f.Fuzz(func(t *testing.T, n int) { Add(n, 1) })\n}\n",
        "func FuzzAdd(_ *testing.F) {\n    Add(1, 1)\n}\n",
        "func FuzzAdd(f *testing.F) {\n    g := f\n    g.Add(1)\n    f.Fuzz(func(t *testing.T, n int) { Add(n, 1) })\n}\n",  # not a call on f itself
        "func FuzzAdd(f *testing.F) {\n    return\n    f.Add(1)\n    f.Fuzz(func(t *testing.T, n int) { Add(n, 1) })\n}\n",
    ],
)
def test_a_fuzz_target_without_a_proven_seed_has_no_id_and_is_named_with_the_reason(make_repo, body):
    result = impact(fuzz_repo(make_repo, body))
    assert ids(result) == []
    assert "seed" in not_listed(result)["example.com/m:FuzzAdd"]


@needs_go_parser
@needs_go_tool
def test_go_fails_a_seeded_fuzz_target_when_the_symbol_breaks(make_repo):
    assert run_check(fuzz_repo(make_repo, SEEDED, BROKEN_LIB), tests=["fz_test.go::FuzzAdd"], profile=GO)["passed"] is False


@needs_go_parser
@needs_go_tool
def test_go_passes_a_seedless_fuzz_target_with_the_symbol_broken(make_repo):
    seedless = "func FuzzAdd(f *testing.F) {\n    f.Fuzz(func(t *testing.T, n int) {\n        if Add(n, 1) != n+1 {\n            t.Fatal(\"broken\")\n        }\n    })\n}\n"
    result = run_check(fuzz_repo(make_repo, seedless, BROKEN_LIB), tests=["fz_test.go::FuzzAdd"], profile=GO)
    assert result["passed"] is True  # the false green the rule exists for: this is the id ripple no longer hands out


# ---- a name declared twice, wherever the second one is -----------------------------------------------------------


@needs_go_parser
def test_a_name_a_constrained_file_declares_again_withholds_the_id_of_the_one_every_build_has(make_repo):
    repo = make_repo({k: v for k, v in M1.items() if k in ("go.mod", "lib/lib.go", "dup/a_test.go", "dup/b_test.go", "dup/dup.go")})
    result = impact(repo, "example.com/m1/lib:Target")
    assert ids(result) == []
    reasons = not_listed(result)
    assert "more than once" in reasons["example.com/m1/dup:TestD"]


@needs_go_parser
@needs_go_tool
def test_go_runs_both_test_ds_so_the_listed_ones_failure_would_be_lost(make_repo):
    repo = make_repo(M1_BROKEN)
    status = subprocess.run(["go", "test", "-list", ".", "./dup"], cwd=repo, capture_output=True, text=True)
    assert status.stdout.split().count("TestD") == 2


# ---- one rule for a lowercase letter, in the collection and in check ----------------------------------------------


@pytest.mark.parametrize("letter", ["ʰ", "ª", "ᴬ", "ǅ", "日", "É", "_", "1"])
def test_check_takes_every_name_go_takes_as_a_test(letter):
    from ripple.go_check import is_test_name

    assert is_test_name(f"Test{letter}") and is_test_name(f"Fuzz{letter}") and is_test_name(f"Example{letter}")


@pytest.mark.parametrize("letter", ["é", "ǆ", "ℓ", "a"])
def test_check_refuses_every_name_go_does_not_take_as_a_test(letter):
    from ripple.go_check import is_test_name

    assert not is_test_name(f"Test{letter}")
