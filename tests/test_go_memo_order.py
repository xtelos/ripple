"""The Go graph of one set of file contents must not depend on the order packages are resolved, nor on which call warmed a memo.

Package `q` and its external test package `q_test` are two Package objects for one directory. A `_test.go` file of `q` is
in every build that has a file of `q_test` (the test binary of the directory has both), and the resolver used to say so for
a call in `q`'s own tests and to doubt it for a call in `q_test`. Three memos (result types, interface shapes, concrete
types) then kept the answer of whoever asked first under a key that did not say who asked.

The order lever in these layouts is the package id: two go.mod files of one module path make the second directory's package
fall back to its directory name, so swapping which directory holds the module under test swaps which package id sorts
first, with identical file contents. A damaged file in the directory (a parse error after the package clause, or one
ripple cannot open) is what makes a name in the directory doubtful at all, so every layout has one.

Every answer was first settled by `go test -v` and `go vet` on the same sources with the damaged file replaced by a legal
one (test_go_runs_what_ripple_draws repeats the run whenever a Go toolchain is on PATH).

These need tree-sitter-go (pip install 'ripple-code[go]') and are skipped without it.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import textwrap

import pytest

from ripple.indexer import build_graph
from ripple.profile import GO

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

GOMOD = "module m\n\ngo 1.21\n"
DAMAGE = {"parse error": "incomplete", "unreadable": "chmod"}


def write(root, files: dict[str, str]) -> None:
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source))


def damage(root, rel: str, package: str, kind: str) -> None:
    """The file ripple cannot read whole: `incomplete` is a top-level parse error with the package clause intact, `chmod`
    a file with no read permission (no tree at all, so it may belong to either package of its directory), `none` a legal
    stand-in for the Go toolchain."""
    path = root / rel
    if kind == "incomplete":
        path.write_bytes(f"package {package}\n\nfunc ( {{\n".encode())
    else:
        path.write_text(f"package {package}\n")
        if kind == "chmod":
            os.chmod(path, 0)


# ---- result types: an alias declared in an in-package test file, read by the package's own tests and its external tests ----


def result_layout(root, target: str, other: str, kind: str, warm: bool = True, extra: dict[str, str] | None = None):
    """Two modules both named `m`: `target` holds package q (B, an alias A of it and a method returning A, all in q's test file)
    and its external test package, `other` a trivial package. TestIn is q's own test, TestExt the external one."""
    call = "B{}.Mk().M()" if warm else "_ = t"
    write(
        root,
        {
            f"{target}/go.mod": GOMOD,
            f"{other}/go.mod": GOMOD,
            f"{other}/o.go": "package o\n\nfunc O() {}\n",
            f"{target}/b.go": 'package q\n\nimport "fmt"\n\ntype B struct{}\n\nfunc (B) M() { fmt.Println("B.M") }\n',
            f"{target}/x_test.go": f"""
                package q

                import "testing"

                type A = B

                func (B) Mk() A {{ return B{{}} }}

                func TestIn(t *testing.T) {{ {call} }}
                """,
            f"{target}/ext_test.go": """
                package q_test

                import (
                    "testing"

                    q "m"
                )

                func TestExt(t *testing.T) { q.B{}.Mk().M() }
                """,
            **(extra or {}),
        },
    )
    damage(root, f"{target}/bad.go", "q", kind)
    return root


def answers(graph, *names: str) -> dict[str, tuple[list[str], list[str]]]:
    """For each test function: the methods it has an edge to (`B.M`, the package cut off) and the calls left open."""
    out = {}
    for name in names:
        callers = {e.caller for e in graph.edges if e.caller.rpartition(":")[2] == name}
        assert len(callers) <= 1
        edges = sorted({e.callee.rpartition(":")[2] for e in graph.edges if e.kind != "implements" and e.caller in callers})
        open_ = sorted({u.text for u in graph.unresolved if u.caller.rpartition(":")[2] == name})
        out[name] = (edges, open_)
    return out


BOTH = {"TestIn": (["B.M", "B.Mk"], []), "TestExt": (["B.M", "B.Mk"], [])}


@pytest.mark.parametrize("kind", DAMAGE.values(), ids=DAMAGE.keys())
@pytest.mark.parametrize("target,other", [("a", "z"), ("z", "a")], ids=["target first", "target last"])
def test_one_module_gives_one_answer_whatever_the_directories_are_called(tmp_path, target, other, kind):
    # go test: TestIn and TestExt both print B.M (the test binary of q has x_test.go, and q_test links it)
    graph = build_graph(result_layout(tmp_path, target, other, kind), GO)
    assert answers(graph, "TestIn", "TestExt") == BOTH


@pytest.mark.parametrize("target,other", [("a", "z"), ("z", "a")], ids=["target first", "target last"])
def test_a_call_nobody_made_does_not_change_the_answer_of_another(tmp_path, target, other):
    # TestIn makes no call here: TestExt, the first call to read the alias, must still follow it
    graph = build_graph(result_layout(tmp_path, target, other, "incomplete", warm=False), GO)
    assert answers(graph, "TestIn", "TestExt") == {"TestIn": ([], []), "TestExt": (["B.M", "B.Mk"], [])}


# ---- what a doubtful name stays when the call is NOT in a build that has the declaring test file ----


LIBRARY = {
    # a library file of q: some builds (go build) do not have x_test.go, so what A names is not settled for it
    "{t}/c.go": """
        package q

        func Lib() { B{}.Mk().M() }
        """,
    # a test of another directory: q's test files are not in its build (and `go vet` says Mk is undefined)
    "{t}/p/p_test.go": """
        package p

        import (
            "testing"

            q "m"
        )

        func TestOther(t *testing.T) { q.B{}.Mk().M() }
        """,
}


@pytest.mark.parametrize("target,other", [("a", "z"), ("z", "a")], ids=["target first", "target last"])
def test_a_library_file_and_another_directorys_test_get_no_edge_to_what_only_q_s_tests_declare(tmp_path, target, other):
    extra = {rel.format(t=target): source for rel, source in LIBRARY.items()}
    graph = build_graph(result_layout(tmp_path, target, other, "incomplete", extra=extra), GO)
    assert answers(graph, "Lib", "TestOther") == {"Lib": ([], ["B{}.Mk", "B{}.Mk().M"]), "TestOther": ([], ["q.B{}.Mk", "q.B{}.Mk().M"])}
    assert answers(graph, "TestIn", "TestExt") == BOTH


# ---- the memo is not keyed by who asked: a function of one package, read by the tests of two ----------------------------


def owner_layout(root, owner: str, reader: str, kind: str):
    """`owner` (package o) returns an alias that only its test file declares (so only o's own test binary compiles it) and its
    own test calls it; `reader` (package rd) has a test that calls the same function. The directory names decide who is
    resolved first."""
    write(
        root,
        {
            "go.mod": GOMOD,
            f"{owner}/b.go": 'package o\n\nimport "fmt"\n\ntype B struct{}\n\nfunc (B) M() { fmt.Println("B.M") }\n\nfunc New() A { return B{} }\n',
            f"{owner}/x_test.go": """
                package o

                import "testing"

                type A = B

                func TestOwn(t *testing.T) { New().M() }
                """,
            f"{reader}/r_test.go": """
                package rd

                import (
                    "testing"

                    "m/OWNER"
                )

                func TestRead(t *testing.T) { o.New().M() }
                """.replace("OWNER", owner),
        },
    )
    damage(root, f"{owner}/bad.go", "o", kind)
    return root


@pytest.mark.parametrize("kind", DAMAGE.values(), ids=DAMAGE.keys())
@pytest.mark.parametrize("owner,reader", [("a", "z"), ("z", "a")], ids=["owner first", "owner last"])
def test_a_result_type_is_not_followed_for_one_caller_because_another_could_follow_it(tmp_path, owner, reader, kind):
    # go test: o's own TestOwn prints B.M; rd's TestRead does not compile (o is built without x_test.go, so `A` is undefined)
    graph = build_graph(owner_layout(tmp_path, owner, reader, kind), GO)
    assert answers(graph, "TestOwn", "TestRead") == {"TestOwn": (["B.M", "New"], []), "TestRead": (["New"], ["o.New().M"])}


# ---- interface shapes: an interface that embeds an alias of a test file, asked by the package and by its external tests ---


def shape_layout(root, target: str, other: str, kind: str):
    write(
        root,
        {
            f"{target}/go.mod": GOMOD,
            f"{other}/go.mod": GOMOD,
            f"{other}/o.go": "package o\n\nfunc O() {}\n",
            f"{target}/s/s.go": """
                package s

                type K interface {
                    Run()
                    Go()
                }

                type Iface2 interface {
                    Stop()
                }

                type Iface3 interface {
                    Halt()
                }
                """,
            f"{target}/r.go": "package r\n\nfunc R() {}\n",
            f"{target}/x_test.go": """
                package r

                import (
                    "testing"

                    "m/s"
                )

                type A = s.Iface2

                type A0 = s.Iface3

                func TestIn(t *testing.T) {
                    var k s.K
                    if k != nil {
                        k.Go()
                    }
                }
                """,
            f"{target}/ext_test.go": """
                package r_test

                import (
                    "fmt"
                    "testing"

                    r "m"
                    "m/s"
                )

                type J interface {
                    Run()
                    r.A
                }

                type T struct{ J }

                func (T) Go() { fmt.Println("T.Go") }

                type T2 r.A0

                func TestExt(t *testing.T) {
                    var k s.K = T{}
                    k.Go()
                }
                """,
        },
    )
    damage(root, f"{target}/bad.go", "r", kind)
    return root


def implements(graph) -> list[tuple[str, str]]:
    return sorted((e.caller.rpartition(":")[2], e.callee.rpartition(":")[2]) for e in graph.edges if e.kind == "implements")


@pytest.mark.parametrize("target,other", [("a", "z"), ("z", "a")], ids=["target first", "target last"])
def test_the_implements_edges_do_not_depend_on_which_package_is_resolved_first(tmp_path, target, other):
    # go vet and go test: `var k s.K = T{}` compiles and TestExt prints T.Go, so T.Go is a method a call of K.Go may run in the
    # test binary of the directory, for TestIn (package r) and TestExt (package r_test) alike
    graph = build_graph(shape_layout(tmp_path, target, other, "incomplete"), GO)
    assert implements(graph) == [("TestExt", "T.Go"), ("TestIn", "T.Go")]


# ---- the export_test.go idiom: what the same directory's external tests call, with a damaged file in the package ----------


def export_layout(root, kind: str, callers: tuple[str, ...] = ("lib", "other")):
    """k's in-package test file exports a helper; k's own tests (in-package and external) call it. So may a library file
    of k and a test of another directory, which have no such build (each of them stops k, or p, building under Go, so the
    toolchain is shown them one at a time: `callers`)."""
    files = {
        "go.mod": GOMOD,
        "k/k.go": 'package k\n\ntype T struct{}\n\nfunc (T) Hello() string { return "T.Hello" }\n',
        "k/export_test.go": "package k\n\nfunc Helper() string { return T{}.Hello() }\n",
        "k/in_test.go": 'package k\n\nimport "testing"\n\nfunc TestIn(t *testing.T) { t.Log(Helper()) }\n',
        "k/ext_test.go": 'package k_test\n\nimport (\n\t"testing"\n\n\t"m/k"\n)\n\nfunc TestHelper(t *testing.T) { t.Log(k.Helper()) }\n',
    }
    if "lib" in callers:
        files["k/lib.go"] = "package k\n\nfunc Lib() string { return Helper() }\n"
    if "other" in callers:
        files["p/p_test.go"] = 'package p\n\nimport (\n\t"testing"\n\n\t"m/k"\n)\n\nfunc TestOther(t *testing.T) { t.Log(k.Helper()) }\n'
    write(root, files)
    damage(root, "k/bad.go", "k", kind)
    return root


@pytest.mark.parametrize("kind", DAMAGE.values(), ids=DAMAGE.keys())
def test_the_external_test_package_calls_what_its_packages_test_file_exports(tmp_path, kind):
    # go test: TestHelper and TestIn both run Helper. A library file of k, and a test of directory p, are in no build that has it.
    graph = build_graph(export_layout(tmp_path, kind), GO)
    assert answers(graph, "TestHelper", "TestIn", "Lib", "TestOther") == {
        "TestHelper": (["Helper"], []),
        "TestIn": (["Helper"], []),
        "Lib": ([], ["Helper"]),
        "TestOther": ([], ["k.Helper"]),
    }


# ---- a fully readable repo: the list of declarations a call is given depends on the caller, so a memo of it must too ----
#
# `T.M` is declared for linux in d/m_linux.go and, with another result type, in the windows-only test file
# d/m_windows_test.go. A call in e's constrained test file (e_linux_test.go) does not have the second (another directory's
# test file is in no build of e's tests), while a library file of e (e_linux.go) is given both by the walk. Both calls start
# from the same first declaration. Whichever was resolved first decided what the other was told, and the order is a file name.


def variants_layout(root, library: str | None = "e_linux.go"):
    files = {
        "go.mod": GOMOD,
        "d/t.go": """
            package d

            import "fmt"

            type T struct{}

            type R1 struct{}

            func (R1) N() { fmt.Println("R1.N") }

            type R2 struct{}

            func (R2) N() { fmt.Println("R2.N") }
            """,
        "d/m_linux.go": "package d\n\nfunc (T) M() R1 { return R1{} }\n",
        "d/m_windows_test.go": "package d\n\nfunc (T) M() R2 { return R2{} }\n",
        "e/e_linux_test.go": 'package e\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestE(t *testing.T) { d.T{}.M().N() }\n',
    }
    if library is not None:
        files[f"e/{library}"] = 'package e\n\nimport "m/d"\n\nfunc Lib() { d.T{}.M().N() }\n'
    write(root, files)
    return root


VARIANTS = {"library file first": "e_linux.go", "library file last": "z_linux.go", "no library file": None}


@pytest.mark.parametrize("library", VARIANTS.values(), ids=VARIANTS.keys())
def test_a_test_file_is_given_the_same_answer_whichever_call_was_resolved_before_it(tmp_path, library):
    # go test ./e: TestE prints R1.N (d's windows test file is not in the build, and neither is it in any build of e)
    graph = build_graph(variants_layout(tmp_path, library), GO)
    assert answers(graph, "TestE") == {"TestE": (["R1.N", "T.M"], [])}


@pytest.mark.parametrize("library", ["e_linux.go", "z_linux.go"], ids=["library file first", "library file last"])
def test_two_callers_given_different_declarations_do_not_share_an_answer_when_a_test_binary_links_the_library_file(tmp_path, library):
    # d's external test imports e, so d's test binary links Lib (built against d WITH its test files: both variants of M, which
    # is left open) while TestE, a test of e, never has d's test files. Both read the same first declaration of M.
    root = variants_layout(tmp_path, library)
    write(root, {"d/x_linux_test.go": 'package d_test\n\nimport (\n\t"testing"\n\n\t"m/e"\n)\n\nfunc TestX(t *testing.T) { e.Lib() }\n'})
    graph = build_graph(root, GO)
    assert answers(graph, "TestE", "Lib") == {"TestE": (["R1.N", "T.M"], []), "Lib": (["T.M", "T.M#2"], ["d.T{}.M().N"])}


def test_a_library_call_is_given_the_same_answer_whichever_file_name_orders_it(tmp_path):
    first = answers(build_graph(variants_layout(tmp_path / "first", "e_linux.go"), GO), "Lib")
    last = answers(build_graph(variants_layout(tmp_path / "last", "z_linux.go"), GO), "Lib")
    assert first == last


# ---- a library file of one directory and another directory's _test.go declarations -----------------------------------
#
# A declaration in a _test.go file of directory d is in no build of any file outside d: the test files of a package are
# compiled into its own test binary and its external test package's, and into no other build. PR #40 left them out for a
# caller that is itself a _test.go file; a library file of another directory has none of them either.


def foreign_layout(root, library: bool = True, only_test: bool = False):
    """`T.M` is `R1` for linux in a library file of d and `R2` in a windows-only test file of d (valid Go, a variant of the
    method for each build). Callers: a library file and a test file of e, and d's own library file, tests and external tests.
    With `only_test`, the method is declared in d's test file and nowhere else."""
    files = {
        "go.mod": GOMOD,
        "d/t.go": """
            package d

            import "fmt"

            type T struct{}

            type R1 struct{}

            func (R1) N() { fmt.Println("R1.N") }

            type R2 struct{}

            func (R2) N() { fmt.Println("R2.N") }
            """,
        "e/e_linux_test.go": 'package e\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestE(t *testing.T) { d.T{}.M().N() }\n',
        "d/in_linux_test.go": 'package d\n\nimport "testing"\n\nfunc TestIn(t *testing.T) { T{}.M().N() }\n',
        "d/ext_linux_test.go": 'package d_test\n\nimport (\n\t"testing"\n\n\t"m/d"\n)\n\nfunc TestExt(t *testing.T) { d.T{}.M().N() }\n',
    }
    if only_test:
        files["d/m_test.go"] = "package d\n\nfunc (T) M() R2 { return R2{} }\n"
    else:
        files["d/m_windows_test.go"] = "package d\n\nfunc (T) M() R2 { return R2{} }\n"
        files["d/m_linux.go"] = "package d\n\nfunc (T) M() R1 { return R1{} }\n"
        files["d/own.go"] = "package d\n\nfunc Own() { T{}.M().N() }\n"  # built for windows without d's M: go test only
    if library:
        files["e/e_linux.go"] = 'package e\n\nimport "m/d"\n\nfunc Lib() { d.T{}.M().N() }\n'
    write(root, files)
    return root


def test_a_library_file_gets_no_edge_to_a_method_only_another_directorys_tests_declare(tmp_path):
    # go build: Lib has d's linux M (R1); `GOOS=windows go test` of d has the other M, which e's build never has
    graph = build_graph(foreign_layout(tmp_path), GO)
    got = answers(graph, "Lib", "TestE")
    assert got == {"Lib": (["R1.N", "T.M"], []), "TestE": (["R1.N", "T.M"], [])}, "no edge to T.M#2, the declaration in d/m_windows_test.go"


def test_the_directory_that_declares_the_test_file_still_has_both_variants(tmp_path):
    # d's library file (some builds have the windows test file), d's tests and d's external tests: both M, which is not settled
    graph = build_graph(foreign_layout(tmp_path), GO)
    for name in ("Own", "TestIn", "TestExt"):
        assert answers(graph, name)[name] == (["T.M", "T.M#2"], ["T{}.M().N" if name != "TestExt" else "d.T{}.M().N"]), name


def test_a_library_file_gets_no_edge_to_a_method_that_only_another_directorys_tests_declare_at_all(tmp_path):
    # go vet ./e: d.T{}.M undefined (d is built without its test files)
    graph = build_graph(foreign_layout(tmp_path, only_test=True), GO)
    got = answers(graph, "Lib", "TestE")
    assert got == {"Lib": ([], ["d.T{}.M", "d.T{}.M().N"]), "TestE": ([], ["d.T{}.M", "d.T{}.M().N"])}


def linked_layout(root, how: str | None):
    """b's test file overrides Conn.Close, which Conn otherwise promotes from Base. a's library file calls it. `how` says how
    b's tests reach a: None (they do not), "plain" (a test file with no constraint imports a), "tagged" (only a linux test file does)."""
    files = {
        "go.mod": GOMOD,
        "b/b.go": 'package b\n\ntype Base struct{}\n\nfunc (Base) Close() string { return "Base.Close" }\n\ntype Conn struct{ Base }\n',
        "b/b_test.go": 'package b\n\nfunc (Conn) Close() string { return "Conn.Close" }\n',
        "a/a.go": 'package a\n\nimport "m/b"\n\nfunc Direct(c b.Conn) string { return c.Close() }\n',
    }
    if how in ("plain", "tagged"):
        name = "ext_test.go" if how == "plain" else "ext_linux_test.go"
        files[f"b/{name}"] = 'package b_test\n\nimport (\n\t"testing"\n\n\t"m/a"\n\t"m/b"\n)\n\nfunc TestUse(t *testing.T) { t.Log("TestUse " + a.Direct(b.Conn{})) }\n'
    elif how == "stdlib":  # tests that import definite answers only: the standard library
        files["b/ext_test.go"] = 'package b_test\n\nimport (\n\t"fmt"\n\t"testing"\n)\n\nfunc TestUse(t *testing.T) { t.Log(fmt.Sprint("TestUse")) }\n'
    elif how == "outside":  # a module the repo does not hold (ripple alone: there is no such module offline)
        files["b/ext_test.go"] = 'package b_test\n\nimport (\n\t"testing"\n\n\t"example.com/lib/x"\n)\n\nfunc TestUse(t *testing.T) { t.Log(x.Name) }\n'
    write(root, files)
    return root


def test_a_library_file_is_left_open_when_the_test_binary_of_the_other_directory_may_link_it(tmp_path):
    # go test ./b: TestUse runs Conn.Close (a is built against b with its test files); go build: Direct runs Base.Close
    for how in ("plain", "tagged"):
        graph = build_graph(linked_layout(tmp_path / how, how), GO)
        assert answers(graph, "Direct")["Direct"] == ([], ["c.Close"]), how


def test_a_library_file_gets_the_promoted_method_when_no_test_binary_links_it_with_the_other_directorys_tests(tmp_path):
    # go test ./a and go build: Direct runs Base.Close; b's tests do not import a, so b's test binary never builds it
    graph = build_graph(linked_layout(tmp_path, None), GO)
    assert answers(graph, "Direct")["Direct"] == (["Base.Close"], [])


def test_go_runs_the_override_in_the_binary_that_links_the_library_file_and_not_otherwise(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    for how in ("plain", "tagged"):
        root = linked_layout(tmp_path / how, how)
        ran = printed(go, root, root)
        assert any(line.endswith("TestUse Conn.Close") for line in ran["TestUse"]), ran
    root = linked_layout(tmp_path / "none", None)
    (root / "a" / "a_test.go").write_text('package a\n\nimport (\n\t"testing"\n\n\t"m/b"\n)\n\nfunc TestDirect(t *testing.T) { t.Log("TestDirect " + Direct(b.Conn{})) }\n')
    ran = printed(go, root, root)
    assert any(line.endswith("TestDirect Base.Close") for line in ran["TestDirect"]), ran


def test_go_builds_the_library_caller_without_d_s_test_files(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    root = foreign_layout(tmp_path / "both")
    run = lambda *args, goos="linux": subprocess.run([go, *args], cwd=root, env={**go_env(root), "GOOS": goos}, capture_output=True, text=True)  # noqa: E731
    assert run("build", "./...").returncode == 0, "linux: d has its linux M, e's Lib and d's Own build with it"
    ran = printed(go, root, root)
    assert "R1.N" in ran["TestE"] and "R1.N" in ran["TestIn"] and "R1.N" in ran["TestExt"], ran
    # windows: d's own library file builds only together with d's test file (the method is the other one), e has no file at all
    assert "T{}.M undefined" in run("build", "./d", goos="windows").stderr
    assert run("test", "-c", "-o", "/dev/null", "./d", goos="windows").returncode == 0
    # the method declared in d's test file and nowhere else: d's own tests have it, e's library file does not
    only = foreign_layout(tmp_path / "only", only_test=True)
    ran = printed(go, only, only)
    assert "R2.N" in ran["TestIn"] and "R2.N" in ran["TestExt"], ran
    vetted = subprocess.run([go, "vet", "./e"], cwd=only, env=go_env(only), capture_output=True, text=True)
    assert vetted.returncode != 0 and "d.T{}.M undefined" in vetted.stderr, vetted.stderr


# ---- the same answers from the toolchain itself ---------------------------------------------------------------------


def go_env(root) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "HOME": str(root), "GOCACHE": str(root / ".gocache"), "GOPATH": str(root / ".gopath"), "GOPROXY": "off", "GOENV": "off", "GOFLAGS": "", "GOWORK": "off", "CGO_ENABLED": "0"}


def printed(go: str, directory, root) -> dict[str, list[str]]:
    """What each test of `go test -json ./...` printed."""
    run = subprocess.run([go, "test", "-count=1", "-json", "./..."], cwd=directory, env=go_env(root), capture_output=True, text=True)
    logged: dict[str, list[str]] = {}
    for line in run.stdout.splitlines():
        event = json.loads(line)
        if event.get("Action") == "output" and event.get("Test"):
            logged.setdefault(event["Test"], []).append(event["Output"].strip())
    return logged


def test_go_runs_what_ripple_draws(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    root = result_layout(tmp_path / "own", "a", "z", "none")
    ran = printed(go, root / "a", root)
    assert "B.M" in ran["TestIn"] and "B.M" in ran["TestExt"], ran
    # the library file does not build (`go build` has no x_test.go), and neither does a test of another directory (p's build has no q test files)
    library = result_layout(tmp_path / "lib", "a", "z", "none", extra={"a/c.go": LIBRARY["{t}/c.go"]})
    built = subprocess.run([go, "build", "./..."], cwd=library / "a", env=go_env(library), capture_output=True, text=True)
    assert built.returncode != 0 and "c.go" in built.stderr and "B{}.Mk undefined" in built.stderr, built.stderr
    other = result_layout(tmp_path / "other", "a", "z", "none", extra={"a/p/p_test.go": LIBRARY["{t}/p/p_test.go"]})
    vetted = subprocess.run([go, "vet", "./p"], cwd=other / "a", env=go_env(other), capture_output=True, text=True)
    assert vetted.returncode != 0 and "p_test.go" in vetted.stderr and "q.B{}.Mk undefined" in vetted.stderr, vetted.stderr


def test_go_runs_the_owner_and_cannot_build_the_reader(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    root = owner_layout(tmp_path, "a", "z", "none")
    (root / "a" / "bad.go").write_text("package o\n")
    ran = printed(go, root, root)
    assert "B.M" in ran["TestOwn"], ran
    reader = subprocess.run([go, "vet", "./z"], cwd=root, env=go_env(root), capture_output=True, text=True)
    assert reader.returncode != 0 and "undefined: A" in reader.stderr


def test_go_runs_the_method_the_implements_edge_names(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    root = shape_layout(tmp_path, "a", "z", "none")
    ran = printed(go, root / "a", root)
    assert "T.Go" in ran["TestExt"], ran
    vetted = subprocess.run([go, "vet", "./..."], cwd=root / "a", env=go_env(root), capture_output=True, text=True)
    assert vetted.returncode == 0, vetted.stderr


def test_go_runs_the_helper_its_packages_test_file_exports(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    root = export_layout(tmp_path / "own", "none", callers=())
    ran = printed(go, root, root)
    for test in ("TestHelper", "TestIn"):
        assert any(line.endswith("T.Hello") for line in ran[test]), ran
    library = export_layout(tmp_path / "lib", "none", callers=("lib",))
    built = subprocess.run([go, "build", "./..."], cwd=library, env=go_env(library), capture_output=True, text=True)
    assert built.returncode != 0 and "lib.go:3:28: undefined: Helper" in built.stderr, built.stderr
    other = export_layout(tmp_path / "other", "none", callers=("other",))
    vetted = subprocess.run([go, "vet", "./p"], cwd=other, env=go_env(other), capture_output=True, text=True)
    assert vetted.returncode != 0 and "p_test.go" in vetted.stderr and "undefined: k.Helper" in vetted.stderr, vetted.stderr


def test_go_runs_the_variant_the_linux_build_has(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    root = variants_layout(tmp_path)
    ran = printed(go, root, root)
    assert "R1.N" in ran["TestE"], ran
    for goos in ("linux", "windows"):  # both builds type-check: the windows test file is d's own and nobody else's
        env = {**go_env(root), "GOOS": goos}
        vetted = subprocess.run([go, "vet", "./d"], cwd=root, env=env, capture_output=True, text=True)
        assert vetted.returncode == 0, vetted.stderr


# ---- an import ripple cannot place may name the library file: the test binary may link it -------------------------------
#
# test_binary_may_link is an upper bound, and dropping another directory's _test.go declaration is only right when the file is
# outside it. An import that resolve_in answers MISSING (it cannot tell which directory the build uses) may name any package of
# the repo, so the bound is unknown and the declaration stays. OUTSIDE (no module of the repo holds the path) and PUBLISHED
# name no directory of the repo, and the drop stays. Both cases below are valid Go in which the library file x.Lib runs the
# method d's export_test.go declares, because d's external test links it.


def vendored_layout(root, linked: bool = True):
    """m is vendored, replaces n with ../n, and d's external test imports n/x; n requires m and replaces it with ../m. n/x.Lib
    calls d.T.M, which export_test.go overrides (T embeds S, which has M). `go test -mod=mod ./d` in m links n/x from the
    checkout, built against d with its test files. `linked=False`: d's tests import nothing of n."""
    lib = 'package x\n\nimport "m/d"\n\nfunc Lib() string { return d.T{}.M() + " " + d.Only() }\n'
    write(
        root,
        {
            "m/go.mod": "module m\n\ngo 1.22\n\nrequire n v0.0.0\n\nreplace n => ../n\n",
            "m/vendor/modules.txt": "# n v0.0.0 => ../n\n## explicit; go 1.22\nn/x\n# n => ../n\n",
            "m/vendor/n/x/x.go": lib,
            "m/d/t.go": 'package d\n\ntype S struct{}\n\nfunc (S) M() string { return "S.M" }\n\ntype T struct{ S }\n',
            "m/d/export_test.go": 'package d\n\nfunc (T) M() string { return "T.M(test)" }\n\nfunc Only() string { return "Only" }\n',
            "m/d/d_test.go": 'package d_test\n\nimport (\n\t"testing"\n\n\t"n/x"\n)\n\nfunc TestX(t *testing.T) { t.Log(x.Lib()) }\n',
            "n/go.mod": "module n\n\ngo 1.22\n\nrequire m v0.0.0\n\nreplace m => ../m\n",
            "n/x/x.go": lib,
        },
    )
    return root


def unplaced_layout(root, readable: bool):
    """d's external test imports m/w, w imports m/x, and x.Lib calls d's overridden method. With `readable` False, w's only file
    has no package clause: the directory holds no package ripple read, so `m/w` names no directory. (`readable` True is the
    twin the toolchain runs.)"""
    write(
        root,
        {
            "go.mod": "module m\n\ngo 1.22\n",
            "d/t.go": 'package d\n\ntype S struct{}\n\nfunc (S) M() string { return "S.M" }\n\ntype T struct{ S }\n',
            "d/export_test.go": 'package d\n\nfunc (T) M() string { return "T.M(test)" }\n\nfunc Only() string { return "Only" }\n',
            "d/d_test.go": 'package d_test\n\nimport (\n\t"testing"\n\n\t"m/w"\n)\n\nfunc TestX(t *testing.T) { t.Log(w.Run()) }\n',
            "w/w.go": 'package w\n\nimport "m/x"\n\nfunc Run() string { return x.Lib() }\n' if readable else "  garbled {{{ \n",
            "x/x.go": 'package x\n\nimport "m/d"\n\nfunc Lib() string { return d.T{}.M() + " " + d.Only() }\n',
        },
    )
    return root


def test_a_library_file_that_a_vendored_module_import_may_name_keeps_the_method_it_may_run(tmp_path):
    # go test -mod=mod -v ./d (in m): TestX logs `T.M(test) Only`. go build ./... in n fails: undefined: d.Only.
    graph = build_graph(vendored_layout(tmp_path), GO)
    # `Only` is declared in d's test file alone and the one build that compiles Lib has it: an edge. `M` is left open.
    assert answers(graph, "Lib")["Lib"] == (["Only"], ["d.T{}.M"]), "an edge to the promoted S.M is an edge to a method the one build of Lib does not run"


def test_a_library_file_that_an_unreadable_directory_may_import_keeps_the_method_it_may_run(tmp_path):
    # go vet ./x (the readable twin): undefined: d.Only. go test -v ./d: TestX logs `T.M(test) Only`.
    graph = build_graph(unplaced_layout(tmp_path, readable=False), GO)
    assert answers(graph, "Lib")["Lib"] == (["Only"], ["d.T{}.M"])


def test_the_drop_stays_when_every_import_of_the_tests_resolves_definitely(tmp_path):
    # Direct runs Base.Close in every build it is in: d's tests (b) import the standard library, or a module the repo does not hold
    for how in (None, "stdlib", "outside"):
        graph = build_graph(linked_layout(tmp_path / str(how), how), GO)
        assert answers(graph, "Direct")["Direct"] == (["Base.Close"], []), how


def test_go_runs_the_method_the_vendored_and_the_unreadable_layouts_link(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    root = vendored_layout(tmp_path / "v1")
    env = {**go_env(root), "GOFLAGS": "-mod=mod"}
    ran = subprocess.run([go, "test", "-count=1", "-v", "./d"], cwd=root / "m", env=env, capture_output=True, text=True)
    assert "T.M(test) Only" in ran.stdout, ran.stdout + ran.stderr
    built = subprocess.run([go, "build", "./..."], cwd=root / "n", env=env, capture_output=True, text=True)
    assert built.returncode != 0 and "undefined: d.Only" in built.stderr, built.stderr
    twin = unplaced_layout(tmp_path / "l1go", readable=True)
    ran = printed(go, twin, twin)
    assert any(line.endswith("T.M(test) Only") for line in ran["TestX"]), ran
    vetted = subprocess.run([go, "vet", "./x"], cwd=twin, env=go_env(twin), capture_output=True, text=True)
    assert vetted.returncode != 0 and "undefined: d.Only" in vetted.stderr, vetted.stderr
