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


def test_a_library_call_is_given_the_same_answer_whichever_file_name_orders_it(tmp_path):
    first = answers(build_graph(variants_layout(tmp_path / "first", "e_linux.go"), GO), "Lib")
    last = answers(build_graph(variants_layout(tmp_path / "last", "z_linux.go"), GO), "Lib")
    assert first == last


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
