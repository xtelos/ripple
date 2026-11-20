"""Which of another directory's _test.go FUNCTIONS a qualified call can run (ripple/go_indexer.py, Resolver.visible).

The test files of a package are compiled into its own test binary (and its external test package's) and into no other
build, except that a test binary links the packages its tests import and builds every one of them against the package
under test WITH its test files. PR #40 and PR #49 taught the walk of a method that. The same rule holds for a function a
call names by its package (`d.F()`): a declaration in a _test.go file of directory d is not there for a _test.go file of
another directory, nor for a library file whose package d's test binary cannot link. Anything that cannot be told is kept.

Every answer below was first settled by running `go build`, `go vet` and `go test` on the same sources
(test_go_runs_what_ripple_draws and the tests after it repeat the runs whenever a Go toolchain is on PATH).

These need tree-sitter-go (pip install 'ripple-code[go]') and are skipped without it.
"""

import importlib.util
import os
import shutil
import subprocess
import textwrap

import pytest

from ripple.indexer import build_graph
from ripple.profile import GO

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

GOMOD = "module m\n\ngo 1.22\n"
NOT_BUILT = "only in _test.go files"  # a part of the reason a call is left open when no build of the caller has the declaration


def write(root, files: dict[str, str]):
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source))
    return root


def answers(graph, *names: str) -> dict[str, tuple[list[str], list[str]]]:
    """For each function: what it has an edge to (`F`, `F#2`, the module cut off) and the calls left open."""
    out = {}
    for name in names:
        edges = sorted({e.callee.removeprefix("m/").partition(":")[2] for e in graph.edges if e.kind != "implements" and e.caller.rpartition(":")[2] == name})
        open_ = sorted({u.text for u in graph.unresolved if u.caller.rpartition(":")[2] == name})
        out[name] = (edges, open_)
    return out


def reasons(graph, name: str, call: str) -> list[str]:
    return [u.reason for u in graph.unresolved if u.caller.rpartition(":")[2] == name and u.text == call]


def go_env(root) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "HOME": str(root), "GOCACHE": str(root / ".gocache"), "GOPATH": str(root / ".gopath"), "GOPROXY": "off", "GOENV": "off", "GOFLAGS": "", "GOWORK": "off", "CGO_ENABLED": "0"}


def go(*args: str, root, goos: str | None = None, cwd=None):
    exe = shutil.which("go")
    if exe is None:
        pytest.skip("the go toolchain is not on PATH")
    env = {**go_env(root), **({"GOOS": goos} if goos else {})}
    return subprocess.run([exe, *args], cwd=cwd or root, env=env, capture_output=True, text=True)


def printed(root, *args: str) -> str:
    ran = go("test", "-count=1", "-v", *args, root=root)
    return ran.stdout + ran.stderr


# ---- the case of the review: F is in a library file of d and in a windows-only test file of d -----------------------------

D_BOTH = {
    "d/f_linux.go": 'package d\n\nfunc F() string { return "F linux" }\n',
    "d/f_windows_test.go": 'package d\n\nfunc F() string { return "F windows test" }\n',
    "d/g_test.go": 'package d\n\nfunc G() string { return "G test only" }\n',
}
E_LIB = 'package e\n\nimport "m/d"\n\nfunc Lib() string { return d.F() }\n'
E_TEST = 'package e\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestE(t *testing.T) { t.Log(d.F()) }\n'


def both_layout(root, lib: bool = True, test: bool = True):
    files = {"go.mod": GOMOD, **D_BOTH}
    if lib:
        files["e/e_linux.go"] = E_LIB
    if test:
        files["e/e_linux_test.go"] = E_TEST
    return write(root, files)


def test_a_library_file_and_a_test_of_another_directory_get_no_edge_to_the_function_only_d_s_test_file_declares(tmp_path):
    # go test -v ./e: `F linux`; GOOS=windows go vet ./d: fine (d's windows test file is d's own and nobody else's)
    graph = build_graph(both_layout(tmp_path), GO)
    assert answers(graph, "Lib", "TestE") == {"Lib": (["F"], []), "TestE": (["F"], [])}, "no edge to F#2, the declaration in d/f_windows_test.go"


def test_the_ids_say_which_declaration_the_edge_is(tmp_path):
    graph = build_graph(both_layout(tmp_path), GO)
    callees = {e.callee for e in graph.edges if e.caller.endswith(":Lib")}
    assert callees == {"m/d:F"}
    assert graph.symbols["m/d:F#2"].file == "d/f_windows_test.go"


# ---- a name declared in d's test files alone ------------------------------------------------------------------------------


def only_layout(root, lib: bool = True, test: bool = True, call: str = "d.Only()"):
    files = {"go.mod": GOMOD, "d/d.go": "package d\n", "d/export_test.go": 'package d\n\nfunc Only() string { return "Only" }\n'}
    if lib:
        files["e/e.go"] = f'package e\n\nimport "m/d"\n\nfunc Lib() string {{ return {call} }}\n'
    if test:
        files["e/e_test.go"] = f'package e\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestE(t *testing.T) {{ t.Log({call}) }}\n'
    return write(root, files)


def test_a_function_only_d_s_test_files_declare_is_no_edge_for_another_directory_and_the_reason_says_why(tmp_path):
    # go build ./e: undefined: d.Only; go vet ./e with only e_test.go: undefined: d.Only
    graph = build_graph(only_layout(tmp_path), GO)
    assert answers(graph, "Lib", "TestE") == {"Lib": ([], ["d.Only"]), "TestE": ([], ["d.Only"])}
    for caller in ("Lib", "TestE"):
        (reason,) = reasons(graph, caller, "d.Only")
        assert NOT_BUILT in reason and "undefined" in reason, reason


def test_go_rejects_the_caller_of_a_function_only_d_s_test_files_declare(tmp_path):
    library = only_layout(tmp_path / "lib", test=False)
    built = go("build", "./e", root=library)
    assert built.returncode != 0 and "undefined: d.Only" in built.stderr, built.stderr
    tests = only_layout(tmp_path / "test", lib=False)
    vetted = go("vet", "./e", root=tests)
    assert vetted.returncode != 0 and "e_test.go" in vetted.stderr and "undefined: d.Only" in vetted.stderr, vetted.stderr
    # d's own tests have it
    own = only_layout(tmp_path / "own", lib=False, test=False)
    write(own, {"d/own_test.go": 'package d\n\nimport "testing"\n\nfunc TestOwn(t *testing.T) { t.Log("TestOwn " + Only()) }\n'})
    assert "TestOwn Only" in printed(own, "./d")


def test_go_runs_the_declaration_the_library_has_and_vets_both_platforms_of_d(tmp_path):
    root = both_layout(tmp_path)
    assert "F linux" in printed(root, "./e")
    for goos in ("linux", "windows"):
        vetted = go("vet", "./d", root=root, goos=goos)
        assert vetted.returncode == 0, vetted.stderr


# ---- what is kept: a caller whose package d's test binary links, d's own tests, d's external tests, an import ripple cannot place


def kept_layout(root, how: str = "plain"):
    """d's export_test.go declares Helper and d has F in a linux library file and a windows test file. d's external test imports
    x (`how` "plain"), or w, which imports x (the import path of w is one ripple cannot place when `how` is "unplaced": w has
    no readable package clause); x.Lib calls both. y is imported by nobody's tests. e is a package whose test calls d."""
    files = {
        "go.mod": GOMOD,
        "d/d.go": "package d\n",
        "d/f_linux.go": 'package d\n\nfunc F() string { return "F linux" }\n',
        "d/f_windows_test.go": 'package d\n\nfunc F() string { return "F windows test" }\n',
        "d/export_test.go": 'package d\n\nfunc Helper() string { return "Helper" }\n',
        "d/in_test.go": 'package d\n\nimport "testing"\n\nfunc TestIn(t *testing.T) { t.Log("TestIn " + Helper() + " " + F()) }\n',
        "d/ext_test.go": 'package d_test\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestExt(t *testing.T) { t.Log("TestExt " + d.Helper() + " " + d.F()) }\n',
        "x/x.go": 'package x\n\nimport "m/d"\n\nfunc Lib() string { return d.Helper() + " " + d.F() }\n',
        "y/y.go": 'package y\n\nimport "m/d"\n\nfunc Lib() string { return d.Helper() + " " + d.F() }\n',
        "e/e_test.go": 'package e\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestE(t *testing.T) { t.Log(d.Helper() + " " + d.F()) }\n',
    }
    if how == "plain":
        files["d/use_test.go"] = 'package d_test\n\nimport (\n\t"testing"\n\n\t"m/x"\n)\n\nfunc TestX(t *testing.T) { t.Log("TestX " + x.Lib()) }\n'
    else:
        files["d/use_test.go"] = 'package d_test\n\nimport (\n\t"testing"\n\n\t"m/w"\n)\n\nfunc TestX(t *testing.T) { t.Log("TestX " + w.Run()) }\n'
        files["w/w.go"] = 'package w\n\nimport "m/x"\n\nfunc Run() string { return x.Lib() }\n' if how == "twin" else "  garbled {{{ \n"
    return write(root, files)


def test_a_library_file_that_d_s_tests_link_keeps_both_its_edges(tmp_path):
    # go test -v ./d: TestX logs `Helper F linux` from x.Lib (the test binary builds x against d with d's test files);
    # GOOS=windows builds it against d's windows test file as well
    graph = build_graph(kept_layout(tmp_path), GO)
    callees = {e.callee.removeprefix("m/") for e in graph.edges if e.caller == "m/x:Lib"}
    assert callees == {"d:F", "d:F#2", "d:Helper"}


def test_d_s_own_tests_and_its_external_test_package_keep_the_edge(tmp_path):
    # go test -v ./d: TestIn logs `Helper F linux`, TestExt logs `Helper F linux`
    graph = build_graph(kept_layout(tmp_path), GO)
    for caller in ("m/d:TestIn", "m/d_test:TestExt"):
        callees = {e.callee.removeprefix("m/") for e in graph.edges if e.caller == caller and e.kind != "implements"}
        assert {"d:Helper"} <= callees, (caller, callees)
    assert {e.callee for e in graph.edges if e.caller == "m/d_test:TestExt"} >= {"m/d:Helper", "m/d:F", "m/d:F#2"}


def test_a_library_file_no_test_binary_of_d_links_and_a_test_of_another_directory_get_neither_declaration(tmp_path):
    # go build ./y: undefined: d.Helper; go vet of e's test: undefined: d.Helper
    graph = build_graph(kept_layout(tmp_path), GO)
    for caller in ("m/y:Lib", "m/e:TestE"):
        callees = {e.callee.removeprefix("m/") for e in graph.edges if e.caller == caller and e.kind != "implements"}
        assert callees == {"d:F"}, (caller, callees)
        assert reasons(graph, caller.partition(":")[2], "d.Helper"), caller
    assert not [e for e in graph.edges if e.callee == "m/d:F#2" and e.caller in ("m/y:Lib", "m/e:TestE")]


def test_a_library_file_an_import_ripple_cannot_place_may_name_keeps_both_declarations(tmp_path):
    # the twin below, with w readable, is what go runs: TestX logs `Helper F linux` from x.Lib. Here m/w names no directory ripple read,
    # so the test binary of d may link any package of the repo, x and y included: the edges stay (unknown is kept)
    graph = build_graph(kept_layout(tmp_path, "unplaced"), GO)
    for caller in ("m/x:Lib", "m/y:Lib"):
        callees = {e.callee.removeprefix("m/") for e in graph.edges if e.caller == caller and e.kind != "implements"}
        assert callees == {"d:F", "d:F#2", "d:Helper"}, (caller, callees)
    # a test of another directory is in no test binary of d, whatever d's tests import
    callees = {e.callee.removeprefix("m/") for e in graph.edges if e.caller == "m/e:TestE" and e.kind != "implements"}
    assert callees == {"d:F"}


def test_go_runs_the_test_file_function_from_the_library_file_d_s_tests_link(tmp_path):
    plain = kept_layout(tmp_path / "plain")
    ran = printed(plain, "./d")
    for line in ("TestX Helper F linux", "TestIn Helper F linux", "TestExt Helper F linux"):
        assert line in ran, ran
    built = go("build", "./x", root=plain)
    assert built.returncode != 0 and "undefined: d.Helper" in built.stderr, built.stderr
    twin = kept_layout(tmp_path / "twin", "twin")
    assert "TestX Helper F linux" in printed(twin, "./d")
    # y is imported by no test of d: go builds it against d without d's tests and rejects the call
    built = go("build", "./y", root=twin)
    assert built.returncode != 0 and "undefined: d.Helper" in built.stderr, built.stderr
    # the windows test file's F is in the build of x too, once the test binary of d is built for windows
    compiled = go("test", "-c", "-o", os.devnull, "./d", root=twin, goos="windows")
    assert compiled.returncode == 0, compiled.stderr


# ---- the chain and the value: what F returns is the answer of the declaration the caller can see -------------------------------

RESULT_FILES = {
    "d/d.go": """
        package d

        type R struct{}

        func (R) N() string { return "R.N" }

        type S struct{}

        func (S) N() string { return "S.N" }
        """,
    "d/f_linux.go": "package d\n\nfunc F() R { return R{} }\n",
    "d/f_windows_test.go": "package d\n\nfunc F() S { return S{} }\n",
}


def result_layout(root, library: str = "e_linux.go", linked: bool = False):
    files = {
        "go.mod": GOMOD,
        **RESULT_FILES,
        f"e/{library}": 'package e\n\nimport "m/d"\n\nfunc Lib() string { return d.F().N() }\n\nfunc Value() string {\n\tf := d.F\n\treturn f().N()\n}\n',
        "e/e_linux_test.go": 'package e\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestE(t *testing.T) { t.Log(d.F().N()) }\n',
    }
    if linked:  # d's external test imports e: the test binary of d builds Lib against d WITH its windows test file
        files["d/x_test.go"] = 'package d_test\n\nimport (\n\t"testing"\n\n\t"m/e"\n)\n\nfunc TestX(t *testing.T) { t.Log(e.Lib()) }\n'
    return write(root, files)


def test_the_result_of_the_function_the_caller_sees_decides_the_method_it_calls(tmp_path):
    # go test -v ./e: R.N (S, the result of the windows test file's F, is never in the build of e)
    graph = build_graph(result_layout(tmp_path), GO)
    assert answers(graph, "Lib", "TestE", "Value")["Lib"] == (["F", "R.N"], [])
    assert answers(graph, "TestE")["TestE"] == (["F", "R.N"], [])
    value = answers(graph, "Value")["Value"]
    assert value[0] == ["R.N"] and value[1] == ["f"], "`f := d.F` is a function value: f() is a call of a local, the result type is F's"


@pytest.mark.parametrize("library", ["e_linux.go", "z_linux.go"], ids=["library file first", "library file last"])
def test_two_callers_of_one_function_do_not_share_an_answer_whichever_is_resolved_first(tmp_path, library):
    # d's external test links Lib, so Lib is built against d with the windows test file: F is declared twice, with two results,
    # and the method is left open. TestE never has it, and calls R.N. Both read F from d, and the order is a file name.
    graph = build_graph(result_layout(tmp_path, library, linked=True), GO)
    lib, test = answers(graph, "Lib")["Lib"], answers(graph, "TestE")["TestE"]
    assert lib == (["F", "F#2"], ["d.F().N"])
    assert test == (["F", "R.N"], [])


def test_go_runs_the_method_of_the_result_the_caller_has(tmp_path):
    root = result_layout(tmp_path)
    assert "R.N" in printed(root, "./e")
    for goos in ("linux", "windows"):
        assert go("vet", "./d", root=root, goos=goos).returncode == 0


# ---- the other ways a call names a function of another package: a dot import, and a generic function with two type arguments ----


def named_layout(root, how: str):
    """F (generic G) is in a linux library file of d and in a windows test file of d, which also holds Only (H) alone. e calls
    them bare through a dot import, or as `d.G[int, string](...)` (two type arguments parse as a conversion, the route that
    reads function_named)."""
    if how == "dot":
        names = {"imports": 'import . "m/d"', "spec": '. "m/d"', "f": "F()", "only": "Only()"}
        d = {"d/f_linux.go": 'package d\n\nfunc F() string { return "F linux" }\n', "d/f_windows_test.go": 'package d\n\nfunc F() string { return "F windows test" }\n\nfunc Only() string { return "Only" }\n'}
    else:
        names = {"imports": 'import "m/d"', "spec": '"m/d"', "f": 'd.G[int, string](1, "x")', "only": 'd.H[int, string](1, "x")'}
        d = {
            "d/g_linux.go": 'package d\n\nfunc G[A, B any](a A, b B) string { return "G linux" }\n',
            "d/g_windows_test.go": 'package d\n\nfunc G[A, B any](a A, b B) string { return "G windows test" }\n\nfunc H[A, B any](a A, b B) string { return "H" }\n',
        }
    return write(
        root,
        {
            "go.mod": GOMOD,
            "d/d.go": "package d\n",
            **d,
            "e/e.go": f'package e\n\n{names["imports"]}\n\nfunc Lib() string {{ return {names["f"]} }}\n\nfunc Uses() string {{ return {names["only"]} }}\n',
            "e/e_test.go": f'package e\n\nimport (\n\t"testing"\n\n\t{names["spec"]}\n)\n\nfunc TestE(t *testing.T) {{ t.Log({names["f"]}) }}\n',
        },
    )


@pytest.mark.parametrize("how,only", [("dot", "Only"), ("generic", "d.H")], ids=["dot import", "generic function"])
def test_a_function_named_without_a_qualifier_or_with_type_arguments_is_given_the_declaration_the_caller_has(tmp_path, how, only):
    # go build ./e: Lib and TestE run the linux declaration (`F linux`), and `Only` / `d.H` are undefined (d's test file is not in e's build)
    graph = build_graph(named_layout(tmp_path, how), GO)
    first = "F" if how == "dot" else "G"
    assert answers(graph, "Lib", "TestE")["Lib"] == ([first], []) and answers(graph, "TestE")["TestE"] == ([first], [])
    assert answers(graph, "Uses")["Uses"][0] == [], "no edge to the function d's test file declares alone"


@pytest.mark.parametrize("how,only,ran", [("dot", "undefined: Only", "F linux"), ("generic", "undefined: d.H", "G linux")], ids=["dot import", "generic function"])
def test_go_builds_the_declaration_the_library_has_and_rejects_the_one_only_d_s_test_file_has(tmp_path, how, only, ran):
    root = named_layout(tmp_path, how)
    rejected = go("vet", "./e", root=root)
    assert rejected.returncode != 0 and only in rejected.stderr, rejected.stderr
    (root / "e" / "e.go").write_text((root / "e" / "e.go").read_text().split("func Uses")[0])
    assert go("vet", "./e", root=root).returncode == 0
    assert ran in printed(root, "./e")


# ---- the rule for a library file of d's own directory and the names of d the lookups also serve ------------------------------


def test_d_s_own_library_file_keeps_both_declarations(tmp_path):
    # `GOOS=windows go test ./d` builds d/own.go with d's windows test file, so the call runs either declaration
    root = both_layout(tmp_path, lib=False, test=False)
    write(root, {"d/own.go": "package d\n\nfunc Own() string { return F() + G() }\n", "d/u.go": "package d\n\nfunc Via() string { return Own() }\n"})
    graph = build_graph(root, GO)
    assert answers(graph, "Own")["Own"] == (["F", "F#2", "G"], [])
