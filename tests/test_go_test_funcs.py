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
    them bare through a dot import, or as `d.G[int, string](1, "x")` (a call) or `d.G[int, string](1)` (one argument: the
    grammar reads a conversion, the route that reads function_named)."""
    if how == "dot":
        names = {"imports": 'import . "m/d"', "spec": '. "m/d"', "f": "F()", "only": "Only()"}
        d = {"d/f_linux.go": 'package d\n\nfunc F() string { return "F linux" }\n', "d/f_windows_test.go": 'package d\n\nfunc F() string { return "F windows test" }\n\nfunc Only() string { return "Only" }\n'}
    else:
        params, args = ("a A, b B", '1, "x"') if how == "generic" else ("a A", "1")
        names = {"imports": 'import "m/d"', "spec": '"m/d"', "f": f"d.G[int, string]({args})", "only": f"d.H[int, string]({args})"}
        d = {
            "d/g_linux.go": f'package d\n\nfunc G[A, B any]({params}) string {{ return "G linux" }}\n',
            "d/g_windows_test.go": f'package d\n\nfunc G[A, B any]({params}) string {{ return "G windows test" }}\n\nfunc H[A, B any]({params}) string {{ return "H" }}\n',
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


HOWS = ["dot", "generic", "conversion"]


@pytest.mark.parametrize("how", HOWS)
def test_a_function_named_without_a_qualifier_or_with_type_arguments_is_given_the_declaration_the_caller_has(tmp_path, how):
    # go build ./e: Lib and TestE run the linux declaration (`F linux`), and `Only` / `d.H` are undefined (d's test file is not in e's build)
    graph = build_graph(named_layout(tmp_path, how), GO)
    first = "F" if how == "dot" else "G"
    assert answers(graph, "Lib", "TestE")["Lib"] == ([first], []) and answers(graph, "TestE")["TestE"] == ([first], [])
    assert answers(graph, "Uses")["Uses"][0] == [], "no edge to the function d's test file declares alone"


@pytest.mark.parametrize("how,only,ran", [("dot", "undefined: Only", "F linux"), ("generic", "undefined: d.H", "G linux"), ("conversion", "undefined: d.H", "G linux")], ids=HOWS)
def test_go_builds_the_declaration_the_library_has_and_rejects_the_one_only_d_s_test_file_has(tmp_path, how, only, ran):
    root = named_layout(tmp_path, how)
    rejected = go("vet", "./e", root=root)
    assert rejected.returncode != 0 and only in rejected.stderr, rejected.stderr
    (root / "e" / "e.go").write_text((root / "e" / "e.go").read_text().split("func Uses")[0])
    assert go("vet", "./e", root=root).returncode == 0
    assert ran in printed(root, "./e")


# ---- every route into a name only d's test files declare says so: generic calls, generic and pointer conversions ---------------
#
# The grammar reads `Only[int](1)` and `d.Only[int](1)` (one value argument) as a conversion, `(*d.T)(nil)` as a conversion
# through a pointer type, and a call with a type-argument list as an index expression or a generic type. Each route used to answer
# for a name that is not there with a reason that is not true ("looked up by index", "the callee is computed") or with no record.
# Go reports every one of them undefined, so each is recorded, unresolved, with the reason that no build of the caller has the name.

#           name: (the import, what both callers run, what Go says of it)
ROUTES = {
    "dot import, generic function, one argument": ('import . "m/d"', "_ = Only[int](1)", "undefined: Only"),
    "dot import, generic function, two type arguments": ('import . "m/d"', "_ = Two[int, string](1)", "undefined: Two"),
    "dot import, generic function, two arguments": ('import . "m/d"', '_ = Three[int, string](1, "x")', "undefined: Three"),
    "dot import, generic function, one type argument, two arguments": ('import . "m/d"', "_ = Four[int](1, 2)", "undefined: Four"),
    "dot import, generic type conversion": ('import . "m/d"', "_ = Box[int](Box[int]{})", "undefined: Box"),
    "dot import, pointer conversion": ('import . "m/d"', "_ = (*T)(nil)", "undefined: T"),
    "dot import, plain call": ('import . "m/d"', "_ = Plain()", "undefined: Plain"),
    "qualified, generic function, one argument": ('import "m/d"', "_ = d.Only[int](1)", "undefined: d.Only"),
    "qualified, generic function, two type arguments": ('import "m/d"', "_ = d.Two[int, string](1)", "undefined: d.Two"),
    "qualified, generic function, two arguments": ('import "m/d"', '_ = d.Three[int, string](1, "x")', "undefined: d.Three"),
    "qualified, generic function, one type argument, two arguments": ('import "m/d"', "_ = d.Four[int](1, 2)", "undefined: d.Four"),
    "qualified, generic type conversion": ('import "m/d"', "_ = d.Box[int](d.Box[int]{})", "undefined: d.Box"),
    "qualified, pointer conversion": ('import "m/d"', "_ = (*d.T)(nil)", "undefined: d.T"),
}
ROUTE_NAMES = """
    package d

    func Only[A any](a A) string { return "Only" }

    func Two[A, B any](a A) string { return "Two" }

    func Three[A, B any](a A, b B) string { return "Three" }

    func Four[A any](a A, b int) string { return "Four" }

    func Plain() string { return "Plain" }

    type T struct{}

    type Box[A any] struct{}
    """


def routes_layout(root, case: str, library: bool = True, test: bool = True):
    spec, body, _ = ROUTES[case]
    files = {"go.mod": GOMOD, "d/d.go": "package d\n", "d/names_test.go": textwrap.dedent(ROUTE_NAMES)}
    if library:
        files["e/e.go"] = f"package e\n\n{spec}\n\nfunc Lib() {{\n\t{body}\n}}\n"
    if test:
        files["e/e_test.go"] = f'package e\n\nimport (\n\t"testing"\n\n\t{spec.removeprefix("import ")}\n)\n\nfunc TestE(t *testing.T) {{\n\t{body}\n}}\n'
    return write(root, files)


@pytest.mark.parametrize("case", ROUTES, ids=list(ROUTES))
def test_every_route_into_a_name_only_d_s_test_files_declare_is_left_open_for_that_reason(tmp_path, case):
    graph = build_graph(routes_layout(tmp_path, case), GO)
    for caller in ("Lib", "TestE"):
        assert not [e for e in graph.edges if e.caller.rpartition(":")[2] == caller and e.kind != "implements"], caller
        said = [u.reason for u in graph.unresolved if u.caller.rpartition(":")[2] == caller]
        assert said and all(NOT_BUILT in reason for reason in said), (caller, said)


@pytest.mark.parametrize("case", ROUTES, ids=list(ROUTES))
def test_go_reports_every_route_into_a_name_only_d_s_test_files_declare_undefined(tmp_path, case):
    message = ROUTES[case][2]
    library = go("build", "./e", root=routes_layout(tmp_path / "lib", case, test=False))
    assert library.returncode != 0 and message in library.stderr, library.stderr
    tests = go("vet", "./e", root=routes_layout(tmp_path / "test", case, library=False))
    assert tests.returncode != 0 and message in tests.stderr, tests.stderr


@pytest.mark.parametrize("case", ROUTES, ids=list(ROUTES))
def test_a_file_ripple_cannot_read_may_declare_the_name_and_the_call_is_not_called_undefined(tmp_path, case):
    # d has a file with a parse error after the package clause: it may declare the name in a build of e, so Go may compile the call
    root = routes_layout(tmp_path, case)
    write(root, {"d/bad.go": "package d\n\nfunc ( {\n"})
    graph = build_graph(root, GO)
    for caller in ("Lib", "TestE"):
        said = [u.reason for u in graph.unresolved if u.caller.rpartition(":")[2] == caller]
        assert not any(NOT_BUILT in reason for reason in said), (caller, said)
    said = [u.reason for u in graph.unresolved if u.caller.rpartition(":")[2] == "TestE"]
    assert said, "a test of another directory has no build with d's test files, and the name is open for the file ripple could not read"


SILENT = [c for c in ROUTES if c.startswith("qualified") and "plain" not in c and "two arguments" not in c]


@pytest.mark.xfail(strict=True, reason="KNOWN-WRONG: a generic call or conversion written through a package whose directory has an unreadable file is no record for a library caller")
@pytest.mark.parametrize("case", SILENT, ids=SILENT)
def test_a_library_caller_of_a_qualified_generic_name_in_a_directory_with_an_unreadable_file_is_recorded(tmp_path, case):
    # Not the fault of the name being only in tests: ripple cannot show a library file lacks d's test files when a file of d is unreadable
    # (test_binary_may_link is unknown), so the name is kept, and function_named refuses a name that file may declare another way,
    # which leaves `d.X[int](1)` and `(*d.T)(nil)` with no outcome, as on main for any name of such a package.
    root = routes_layout(tmp_path, case, test=False)
    write(root, {"d/bad.go": "package d\n\nfunc ( {\n"})
    graph = build_graph(root, GO)
    assert [u for u in graph.unresolved if u.caller.endswith(":Lib")], "the call has no record"


def test_a_name_that_a_second_dot_import_may_bind_is_not_called_undefined(tmp_path):
    # `Only` is test-only in d, and w is a dot import ripple cannot read a package of (an outside module): w may declare it, so the
    # call keeps the reason of any name no file ripple read declares, and does not say that Go reports it undefined.
    root = routes_layout(tmp_path, "dot import, generic function, one argument", test=False)
    write(root, {"e/e.go": 'package e\n\nimport (\n\t. "m/d"\n\t. "example.com/lib/w"\n)\n\nfunc Lib() { _ = Only[int](1) }\n'})
    graph = build_graph(root, GO)
    said = [u.reason for u in graph.unresolved if u.caller.endswith(":Lib")]
    assert said and not any(NOT_BUILT in reason for reason in said), said


# ---- the rule for a library file of d's own directory and the names of d the lookups also serve ------------------------------


def test_d_s_own_library_file_keeps_both_declarations(tmp_path):
    # `GOOS=windows go test ./d` builds d/own.go with d's windows test file, so the call runs either declaration
    root = both_layout(tmp_path, lib=False, test=False)
    write(root, {"d/own.go": "package d\n\nfunc Own() string { return F() + G() }\n", "d/u.go": "package d\n\nfunc Via() string { return Own() }\n"})
    graph = build_graph(root, GO)
    assert answers(graph, "Own")["Own"] == (["F", "F#2", "G"], [])


# ---- the other package-level names the same lookups serve: a variable, a constant, a type, a function value ---------------------
#
# One name d's test file declares and nothing else does. Go rejects `d.V`, `d.C`, `d.T` and `d.Only` in a library file of e and in
# a test of e (`undefined: d.V`), so a call through them runs nothing a caller can name, and an edge to a method found through them
# (R.N through the variable's type, Base.M through the type's embedded Base) is an edge from code that does not compile. The same
# call in d's external test package compiles, and keeps the edge.

D_LIB = """
    package d

    type Base struct{}

    func (Base) M() string { return "Base.M" }

    type R struct{}

    func (R) N() string { return "R.N" }

    type U struct{ Base }

    type K int

    func (K) N() string { return "K.N" }
    """

#           name: (the test file of d, what both callers run, what d's external test package's call is an edge to, what Go says)
ONLY = {
    "variable, method called": ("var V = R{}", "_ = d.V.N()", "R.N", "undefined: d.V"),
    "variable, function value called": ("var V = func() string { return Base{}.M() }", "_ = d.V()", None, "undefined: d.V"),
    "constant of a library type": ("const C K = 1", "_ = d.C.N()", None, "undefined: d.C"),
    "type, literal": ("type T struct{ Base }", "_ = d.T{}.M()", "Base.M", "undefined: d.T"),
    "type, variable": ("type T struct{ Base }", "var x d.T\n\t_ = x.M()", "Base.M", "undefined: d.T"),
    "type, conversion": ("type T struct{ Base }", "_ = d.T(d.U{}).M()", "Base.M", "undefined: d.T"),
    "type, pointer conversion": ("type T struct{ Base }", "_ = (*d.T)(nil)", None, "undefined: d.T"),
    "function value": ("func Only() R { return R{} }", "f := d.Only\n\t_ = f().N()", "R.N", "undefined: d.Only"),
}


NAMED_IN_THE_CALL = {"variable, function value called": "d.V", "type, conversion": "d.T"}


def names_layout(root, test_file: str, body: str, library: bool = True, test: bool = True, external: bool = False):
    files = {"go.mod": GOMOD, "d/d.go": textwrap.dedent(D_LIB), "d/names_test.go": f"package d\n\n{test_file}\n"}
    if library:
        files["e/e.go"] = f'package e\n\nimport "m/d"\n\nfunc Lib() {{\n\t{body}\n}}\n'
    if test:
        files["e/e_test.go"] = f'package e\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestE(t *testing.T) {{\n\t{body}\n}}\n'
    if external:
        files["d/ext_test.go"] = f'package d_test\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestExt(t *testing.T) {{\n\t{body}\n}}\n'
    return write(root, files)


@pytest.mark.parametrize("case", ONLY, ids=list(ONLY))
def test_a_name_only_another_directorys_test_file_declares_gives_no_edge_through_it(tmp_path, case):
    test_file, body, _, _ = ONLY[case]
    graph = build_graph(names_layout(tmp_path, test_file, body), GO)
    for caller in ("Lib", "TestE"):
        edges, open_ = answers(graph, caller)[caller]
        assert edges == [] and open_, (caller, edges, open_)
        assert not [e for e in graph.edges if e.caller.rpartition(":")[2] == caller and e.kind != "implements"], caller
    if case in NAMED_IN_THE_CALL:  # the call names the variable or the type itself: the reason is that Go does not know it
        for caller in ("Lib", "TestE"):
            (reason,) = reasons(graph, caller, NAMED_IN_THE_CALL[case])
            assert NOT_BUILT in reason, reason


@pytest.mark.parametrize("case", [c for c in ONLY if ONLY[c][2]], ids=[c for c in ONLY if ONLY[c][2]])
def test_the_external_test_package_of_d_keeps_the_edge_through_the_same_name(tmp_path, case):
    test_file, body, callee, _ = ONLY[case]
    graph = build_graph(names_layout(tmp_path, test_file, body, library=False, test=False, external=True), GO)
    edges, _ = answers(graph, "TestExt")["TestExt"]
    assert callee in edges, edges


@pytest.mark.parametrize("case", ONLY, ids=list(ONLY))
def test_go_rejects_every_caller_of_a_name_only_d_s_test_file_declares_and_compiles_d_s_own(tmp_path, case):
    test_file, body, _, message = ONLY[case]
    library = names_layout(tmp_path / "lib", test_file, body, test=False)
    built = go("build", "./e", root=library)
    assert built.returncode != 0 and message in built.stderr, built.stderr
    tests = names_layout(tmp_path / "test", test_file, body, library=False)
    vetted = go("vet", "./e", root=tests)
    assert vetted.returncode != 0 and message in vetted.stderr, vetted.stderr
    own = names_layout(tmp_path / "own", test_file, body, library=False, test=False, external=True)
    assert go("vet", "./d", root=own).returncode == 0
    assert "--- PASS: TestExt" in printed(own, "./d")


def test_a_library_file_that_d_s_tests_link_keeps_the_edge_through_a_name_only_d_s_tests_declare(tmp_path):
    # go test -v ./d: TestX logs `R.N` (x.Lib is built against d with its test file); go build ./x: undefined: d.V
    root = names_layout(tmp_path, "var V = R{}", "_ = d.V.N()", library=False, test=False)
    write(
        root,
        {
            "x/x.go": 'package x\n\nimport "m/d"\n\nfunc Lib() string { return d.V.N() }\n',
            "d/use_test.go": 'package d_test\n\nimport (\n\t"testing"\n\n\t"m/x"\n)\n\nfunc TestX(t *testing.T) { t.Log("TestX " + x.Lib()) }\n',
        },
    )
    graph = build_graph(root, GO)
    assert answers(graph, "Lib")["Lib"] == (["R.N"], [])
    assert "TestX R.N" in printed(root, "./d")
    assert go("build", "./x", root=root).returncode != 0


def test_a_dot_import_binds_nothing_of_a_type_only_d_s_tests_declare(tmp_path):
    # go vet ./e: undefined: T
    root = names_layout(tmp_path, "type T struct{ Base }", "_ = 1", test=False)
    write(root, {"e/e.go": 'package e\n\nimport . "m/d"\n\nfunc Lib() string { return T{}.M() }\n'})
    graph = build_graph(root, GO)
    assert answers(graph, "Lib")["Lib"][0] == []
    vetted = go("vet", "./e", root=root)
    assert vetted.returncode != 0 and "undefined: T" in vetted.stderr, vetted.stderr


# ---- a type read for the file that writes it, not for the call that asks ----------------------------------------------------------
#
# d's library file returns x.T, a type x's test file declares (valid only in x's test binary, which links d through e: x's external
# test imports e). The result type of `d.Mk` is read once, for d/d.go, and a call in e's test file and a call in e's library file are
# both told it: whether the type is in a build is asked of the file that writes `x.T`, never of whichever caller is resolved first.


def perspective_layout(root, library: str):
    return write(
        root,
        {
            "go.mod": GOMOD,
            "x/x.go": 'package x\n\ntype Base struct{}\n\nfunc (Base) M() string { return "Base.M" }\n',
            "x/t_test.go": "package x\n\ntype T struct{ Base }\n",
            "x/use_test.go": 'package x_test\n\nimport (\n\t"testing"\n\n\t"m/e"\n)\n\nfunc TestUse(t *testing.T) { t.Log("TestUse " + e.Lib()) }\n',
            "d/d.go": 'package d\n\nimport "m/x"\n\nfunc Mk() x.T { return x.T{} }\n',
            f"e/{library}": 'package e\n\nimport "m/d"\n\nfunc Lib() string { return d.Mk().M() }\n',
            "e/e_linux_test.go": 'package e\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestE(t *testing.T) { t.Log(d.Mk().M()) }\n',
        },
    )


def test_a_result_type_is_read_for_the_file_that_writes_it_whichever_caller_is_resolved_first(tmp_path):
    # An ORDER-INDEPENDENCE GUARD, and it asserts nothing about which edges exist. No program Go builds separates the two views of
    # the type (the caller's file and the file that writes `x.T`): d compiles only in builds that have x's test file, so every
    # build of a caller of d is x's test binary, and a caller in it has x's tests whichever file it is in. `go vet ./e` on this layout
    # fails (`undefined: x.T`), so TestE is no program, and its edges are not an answer anyone should read. What must hold is that the
    # resolution order, which the file name sets (the library file sorts before or after the test file), changes no answer.
    first = build_graph(perspective_layout(tmp_path / "first", "e_linux.go"), GO)
    last = build_graph(perspective_layout(tmp_path / "last", "z_linux.go"), GO)
    for name in ("Lib", "TestE"):
        assert answers(first, name) == answers(last, name), name


def test_go_runs_x_s_test_file_type_through_the_library_file_x_s_tests_link(tmp_path):
    root = perspective_layout(tmp_path, "e_linux.go")
    assert "TestUse Base.M" in printed(root, "./x"), "Lib is a program: x's test binary links it"
    built = go("build", "./d", root=root)
    assert built.returncode != 0 and "undefined: x.T" in built.stderr, built.stderr
    vetted = go("vet", "./e", root=root)
    assert vetted.returncode != 0 and "undefined: x.T" in vetted.stderr, "TestE is in no build that compiles: " + vetted.stderr


# ---- KNOWN-WRONG: a name declared in a library file of d and, as another kind, in d's test file --------------------------------
#
# The names of a package are one set per name (Package.names), so a function in d/f_linux.go beside a variable of the same name in
# d/f_windows_test.go reads as a name with two meanings, whichever caller asks. Go compiles e against the library file alone. The
# call stays unresolved, which is never wrong, but its reason ("which one wins is not known") is false for a caller that cannot
# see the test file, and the edge Go proves is lost.


@pytest.mark.xfail(strict=True, reason="KNOWN-WRONG: Package.names holds the kinds of every declaration of a name, whichever caller asks")
def test_a_function_beside_a_variable_of_the_same_name_in_d_s_test_file_is_the_library_function_for_another_directory(tmp_path):
    root = both_layout(tmp_path, lib=True, test=False)
    write(root, {"d/f_windows_test.go": 'package d\n\nvar F = "F windows test"\n'})
    assert go("build", "./e", root=root).returncode == 0, "go builds e against the library F"
    graph = build_graph(root, GO)
    assert answers(graph, "Lib")["Lib"] == (["F"], [])


@pytest.mark.xfail(strict=True, reason="KNOWN-WRONG: a type declared twice is one ambiguous type, whichever caller asks")
def test_a_type_beside_a_test_file_type_of_the_same_name_is_the_library_type_for_another_directory(tmp_path):
    root = write(
        tmp_path,
        {
            "go.mod": GOMOD,
            "d/d.go": textwrap.dedent(D_LIB),
            "d/t_linux.go": "package d\n\ntype T struct{ R }\n",
            "d/t_windows_test.go": "package d\n\ntype T struct{ Base }\n",
            "e/e.go": 'package e\n\nimport "m/d"\n\nfunc Lib() string { return d.T{}.N() }\n',
        },
    )
    assert go("build", "./e", root=root).returncode == 0, "go builds e against the linux T, which embeds R"
    graph = build_graph(root, GO)
    assert answers(graph, "Lib")["Lib"] == (["R.N"], [])
