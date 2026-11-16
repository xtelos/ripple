"""The ids `impact` hands out for Go tests, and what `check` makes of them (no Go needed).

Measured on the current code: a Go repo has no collected tests (`tests` is empty, NoTestCollection),
so impact lists a Go test function among the callers to update, as `<import path>:<TestName>` under
the file it is in. `check` takes that id, and the `file_test.go::TestName` form, for the same test.
"""

import importlib.util
import json
import sys

import pytest

from go_check_support import MODULES, go_argv
from ripple import queries
from ripple.cache import load_repo
from ripple.checker import run_check
from ripple.go_check import find_modules, normalize, parse_wants, plan
from ripple.profile import GO

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
needs_go_parser = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")


@needs_go_parser
def test_impact_lists_a_go_test_as_a_caller_with_a_symbol_id_and_no_test_ids():
    graph = load_repo(MODULES / "twopkgs").graph
    result = queries.impact(graph, "One")
    assert result["tests"] == []
    assert result["by_file"]["a/a_test.go"] == [{"symbol": "example.com/twopkgs/a:TestSame", "line": 5, "distance": 1}]


@needs_go_parser
def test_the_ids_impact_emits_run_the_test_they_name():
    graph = load_repo(MODULES / "twopkgs").graph
    callers = [entry["symbol"] for entries in queries.impact(graph, "One")["by_file"].values() for entry in entries]
    repo = MODULES / "twopkgs"
    asked = normalize(repo, callers)
    assert asked == ["a/a_test.go::TestSame"]
    runs = plan(find_modules(repo), parse_wants(repo, asked))
    assert [run.argv() for run in runs] == [go_argv("./a", run="^(TestSame)$")]


@pytest.mark.parametrize(
    "module, given, asked",
    [
        ("twopkgs", "example.com/twopkgs/a:TestSame", "a/a_test.go::TestSame"),
        ("twopkgs", "example.com/twopkgs/b:TestOnlyB", "b/b_test.go::TestOnlyB"),
        ("subtests", "example.com/subtests:TestTable", "calc_test.go::TestTable"),
        ("subtests", "example.com/subtests:TestTable/bad", "calc_test.go::TestTable/bad"),
        ("multimod", "example.com/multimod/sub:TestSub", "sub/sub_test.go::TestSub"),
        ("multimod", "example.com/multimod:TestRoot", "root_test.go::TestRoot"),
    ],
)
def test_a_symbol_id_is_the_file_and_test_it_names(module, given, asked):
    assert normalize(MODULES / module, [given]) == [asked]


def test_a_test_of_an_external_test_package_is_found_in_its_directory(make_repo):
    repo = make_repo(
        {
            "go.mod": "module example.com/m\n\ngo 1.21\n",
            "x.go": "package m\n",
            "x_ext_test.go": 'package m_test\n\nimport "testing"\n\nfunc TestExt(t *testing.T) {}\n',
        }
    )
    assert normalize(repo, ["example.com/m_test:TestExt"]) == ["x_ext_test.go::TestExt"]


@pytest.mark.parametrize(
    "given",
    [
        "example.com/twopkgs/a:TestNope",  # no file declares it
        "example.com/other/a:TestSame",  # no module of the repo has that path
        "example.com/twopkgs/../../etc:TestSame",  # not a package of the repo
        "example.com/twopkgs/a:Test Same",
        "a/a_test.go::TestSame",  # already the file form
    ],
)
def test_what_is_not_a_symbol_id_of_the_repo_is_left_alone(given):
    assert normalize(MODULES / "twopkgs", [given]) == [given]


def test_an_existing_path_is_never_read_as_a_symbol_id(make_repo):
    repo = make_repo({"go.mod": "module a\n", "a:TestX": "", "b_test.go": 'package a\n\nimport "testing"\n\nfunc TestX(t *testing.T) {}\n'})
    assert normalize(repo, ["a:TestX"]) == ["a:TestX"]


@pytest.fixture
def outside_reads(monkeypatch):
    """Record every path the code lists, reads or probes by Path methods, so a test can say what it touched."""
    from pathlib import Path

    touched = []
    for name in ("iterdir", "read_text", "exists", "is_dir", "is_file", "open"):
        original = getattr(Path, name)

        def spy(self, *args, _original=original, _name=name, **kwargs):
            touched.append(str(self))
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, spy)
    return touched


def outside_dir(tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "o_test.go").write_text('package o\n\nimport "testing"\n\nfunc TestOut(t *testing.T) {}\n')
    return outside


@pytest.mark.parametrize(
    "template",
    [
        "example.com/m//{outside}:TestOut",  # an empty path element makes the directory absolute
        "example.com/m/./{outside}:TestOut",
        "example.com/m/a\\..\\{outside}:TestOut",
        "{outside}:TestOut",  # a path of its own
        "example.com/m/{outside}/../x:TestOut",
    ],
)
def test_a_symbol_id_never_makes_ripple_read_outside_the_repo_and_the_refusal_names_no_outside_path(
    make_repo, tmp_path_factory, outside_reads, template
):
    outside = outside_dir(tmp_path_factory)
    repo = make_repo({"go.mod": "module example.com/m\n\ngo 1.21\n", "x.go": "package m\n"})
    given = template.format(outside=str(outside).lstrip("/") if template.startswith("example.com") else outside)
    del outside_reads[:]
    assert normalize(repo, [given]) == [given]
    with pytest.raises(ValueError) as refused:
        run_check(repo, tests=[given], profile=GO)
    assert str(outside) not in str(refused.value).replace(given, "")
    assert [path for path in outside_reads if path.startswith(str(outside))] == []


def test_a_symlinked_test_file_or_go_mod_pointing_outside_is_never_read(make_repo, tmp_path_factory):
    import os

    outside = outside_dir(tmp_path_factory)
    (outside / "go.mod").write_text("module example.com/leak\n")
    repo = make_repo({"go.mod": "module example.com/m\n\ngo 1.21\n", "x.go": "package m\n", "sub/x.go": "package sub\n"})
    os.symlink(outside / "o_test.go", repo / "link_test.go")
    os.symlink(outside / "go.mod", repo / "sub" / "go.mod")
    # had link_test.go been read, TestOut would have been found in it
    assert normalize(repo, ["example.com/m:TestOut"]) == ["example.com/m:TestOut"]
    # had the go.mod behind the link been read, the module would be example.com/leak
    assert [m.path for m in find_modules(repo) if m.dir == "sub"] == [""]
    with pytest.raises(ValueError):
        run_check(repo, tests=["link_test.go::TestOut"], profile=GO)


def test_a_symbol_id_nothing_resolves_is_refused_not_run(make_repo):
    repo = make_repo({"go.mod": "module example.com/m\n"})
    with pytest.raises(ValueError):
        run_check(repo, tests=["example.com/m:TestGone"], profile=GO)


def test_python_ids_are_untouched_by_all_of_this(make_repo):
    repo = make_repo({"test_a.py": "def test_ok():\n    assert True\n"})
    result = run_check(repo, command=[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], tests=["test_a.py::test_ok"])
    assert result["passed"] is True
    assert json.dumps(result).count("counts") == 0
