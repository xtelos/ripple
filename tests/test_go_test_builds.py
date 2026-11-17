"""Which _test.go declarations a call in a _test.go file can run (ripple/go_indexer.py, Resolver.in_every_build).

The test files of a package are compiled into its own test binary, and into its external test package's (`package foo_test`),
and into no other build. A call in a._test.go file on a value whose type embeds something from package b therefore sees b
without b's _test.go files: a method b declares only in its _test.go is not the one that runs, the promoted one is. The same
directory is another matter (its test binary has every one of its _test.go files), and so is a call in a library file (which
some builds have with the test files and some without).

Every answer below was first settled by running `go test` on this module; test_go_runs_what_ripple_draws repeats that run
whenever a Go toolchain is on PATH.

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

M = "ex.com/m"

FILES = {
    "go.mod": f"module {M}\n\ngo 1.22\n",
    # b: Conn embeds Base. b.go has Base.Close and a library call of c.Close(); b_test.go overrides Close on Conn.
    "b/b.go": """
        package b

        type Base struct{}

        func (Base) Close() string { return "Base.Close" }

        type Conn struct{ Base }

        func Via(c Conn) string { return c.Close() }
        """,
    "b/b_test.go": """
        package b

        import "testing"

        func (Conn) Close() string { return "Conn.Close" }

        func TestSame(t *testing.T) {
            c := Conn{}
            t.Log(c.Close())
        }

        func TestVia(t *testing.T) {
            t.Log(Via(Conn{}))
        }
        """,
    "b/ext_test.go": f"""
        package b_test

        import (
            "testing"

            "{M}/b"
        )

        func TestExternal(t *testing.T) {{
            c := b.Conn{{}}
            t.Log(c.Close())
        }}
        """,
    # d: the override is in a _test.go file that also needs the tag `custom`
    "d/d.go": """
        package d

        type Base struct{}

        func (Base) Close() string { return "Base.Close" }

        type Conn struct{ Base }
        """,
    "d/d_tag_test.go": """
        //go:build custom

        package d

        func (Conn) Close() string { return "Conn.Close" }
        """,
    "d/d_test.go": """
        package d

        import "testing"

        func TestTagged(t *testing.T) {
            c := Conn{}
            t.Log(c.Close())
        }
        """,
    "cmd/m/main.go": f"""
        package main

        import (
            "fmt"

            "{M}/b"
        )

        func main() {{ fmt.Println("via " + b.Via(b.Conn{{}})) }}
        """,
    # a: calls into b and d from its in-package and its external test files
    "a/a.go": "package a\n",
    "a/a_test.go": f"""
        package a

        import (
            "testing"

            "{M}/b"
            "{M}/d"
        )

        func TestA(t *testing.T) {{
            c := b.Conn{{}}
            t.Log(c.Close())
        }}

        func TestAD(t *testing.T) {{
            c := d.Conn{{}}
            t.Log(c.Close())
        }}
        """,
    "a/ax_test.go": f"""
        package a_test

        import (
            "testing"

            "{M}/b"
        )

        func TestAExternal(t *testing.T) {{
            c := b.Conn{{}}
            t.Log(c.Close())
        }}
        """,
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("gotb")
    for rel, source in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source))
    return root


@pytest.fixture(scope="module")
def graph(repo):
    return build_graph(repo, GO)


def callees(graph, caller: str) -> list[str]:
    """What a function calls, as `package:name`, the module path cut off."""
    return sorted(e.callee.removeprefix(f"{M}/") for e in graph.edges if e.kind != "implements" and e.caller == f"{M}/{caller}")


def left_open(graph, caller: str, text: str) -> list[str]:
    return [u.reason for u in graph.unresolved if u.caller == f"{M}/{caller}" and u.text == text]


def test_a_test_in_another_package_runs_the_promoted_method_not_the_override_in_that_packages_tests(graph):
    # go test: "a test calls c.Close() on a b.Conn: Base.Close"
    assert callees(graph, "a:TestA") == ["b:Base.Close"]
    assert left_open(graph, "a:TestA", "c.Close") == []


def test_an_external_test_package_in_another_directory_runs_the_promoted_method_too(graph):
    # go test: package a_test calls c.Close() on a b.Conn: Base.Close
    assert callees(graph, "a_test:TestAExternal") == ["b:Base.Close"]


def test_a_tag_constrained_test_file_of_another_directory_is_no_more_in_the_build_than_a_plain_one(graph):
    # go test and go test -tags custom: a test calls c.Close() on a d.Conn: Base.Close (d's tagged test file is d's alone)
    assert callees(graph, "a:TestAD") == ["d:Base.Close"]


def test_a_test_of_the_same_package_runs_the_override_in_its_own_test_file(graph):
    # go test: b in-package test calls c.Close(): Conn.Close
    assert callees(graph, "b:TestSame") == ["b:Conn.Close"]
    assert left_open(graph, "b:TestSame", "c.Close") == []


def test_the_external_test_package_runs_the_override_in_the_test_files_of_the_package_it_tests(graph):
    # go test: package b_test calls c.Close(): Conn.Close (foo_test links foo together with foo's in-package _test.go files)
    assert callees(graph, "b_test:TestExternal") == ["b:Conn.Close"]
    assert left_open(graph, "b_test:TestExternal", "c.Close") == []


def test_a_library_file_that_calls_a_method_a_test_file_overrides_has_no_proven_callee(graph):
    # go run of a program that calls b.Via: Base.Close; go test of b, whose TestVia calls it: Conn.Close
    assert callees(graph, "b:Via") == []
    reasons = left_open(graph, "b:Via", "c.Close")
    assert len(reasons) == 1 and "only in some builds" in reasons[0]
    assert callees(graph, "b:TestVia") == ["b:Via"], "the call of Via itself is one in every build"


def test_a_test_of_the_package_whose_override_needs_a_tag_has_no_proven_callee(graph):
    # go test: Base.Close; go test -tags custom: Conn.Close
    assert callees(graph, "d:TestTagged") == []
    reasons = left_open(graph, "d:TestTagged", "c.Close")
    assert len(reasons) == 1 and "only in some builds" in reasons[0]


# ---- the same answers from the toolchain itself ---------------------------------------------------------------------


def test_go_runs_what_ripple_draws(graph, repo):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    env = {"PATH": os.environ["PATH"], "HOME": str(repo), "GOCACHE": str(repo / ".gocache"), "GOPATH": str(repo / ".gopath"), "GOPROXY": "off", "GOENV": "off", "GOFLAGS": "", "GOWORK": "off", "CGO_ENABLED": "0"}

    def ran(*flags: str) -> dict[str, str]:
        out = subprocess.run([go, "test", "-count=1", "-json", *flags, "./..."], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout
        logged: dict[str, str] = {}
        for line in out.splitlines():
            event = json.loads(line)
            if event.get("Action") == "output" and event.get("Test") and event["Output"].rstrip().endswith(("Base.Close", "Conn.Close")):
                logged[event["Test"]] = event["Output"].rstrip().rsplit(" ", 1)[-1]
        return logged

    plain, tagged = ran(), ran("-tags", "custom")
    drawn = {}
    for e in graph.edges:
        if e.kind != "implements" and e.callee.endswith((":Base.Close", ":Conn.Close")):
            drawn[e.caller.rsplit(":", 1)[1]] = e.callee.rsplit(":", 1)[1]
    assert drawn == {"TestA": "Base.Close", "TestAD": "Base.Close", "TestAExternal": "Base.Close", "TestSame": "Conn.Close", "TestExternal": "Conn.Close"}
    for test, method in drawn.items():
        assert plain[test] == method and tagged[test] == method, f"{test}: ripple draws {method}, go ran {plain[test]} (tag custom: {tagged[test]})"
    # what ripple leaves open really differs between builds
    assert (plain["TestTagged"], tagged["TestTagged"]) == ("Base.Close", "Conn.Close")
    assert plain["TestVia"] == "Conn.Close"  # b's own test build has b_test.go
    program = subprocess.run([go, "run", "./cmd/m"], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert program == "via Base.Close", "a program built without b's tests runs the promoted method: Via's call is in some builds only"


# ---- a constrained _test.go file and a library file of one package must not share an answer ------------------------------
#
# A call in a library file is in some builds with b's tests and some without; a call in `a_linux_test.go` (constrained, so
# not "tests only", but still a _test.go file) is only in a's test binary, which has b without b's tests. The two files
# read the same type and member and so must never be answered from one cache entry, whichever of them is read first.

ORDERS = {"the test file first": "z.go", "the library file first": "a.go"}


def poisoned(root, library: str, direct: bool = False):
    files = {
        "go.mod": f"module {M}\n\ngo 1.22\n",
        "b/b.go": """
            package b

            type Base struct{}

            func (Base) Close() string { return "Base.Close" }

            type Conn struct{ Base }

            func (Conn) Name() string { return "Conn" }
            """,
        "b/b_test.go": """
            package b

            func (Conn) Close() string { return "Conn.Close" }
            """,
        "b/ext_test.go": f"""
            package b_test

            import (
                "testing"

                "{M}/a"
                "{M}/b"
            )

            func TestUse(t *testing.T) {{
                t.Log("TestUse " + a.Use(b.Conn{{}}))
            }}
            """,
        f"a/{library}": f"""
            package a

            type Closer interface {{
                Close() string
                Name() string
            }}

            func Use(c Closer) string {{ return c.Close() }}
            """,
        "a/a_linux_test.go": f"""
            package a

            import (
                "testing"

                "{M}/b"
            )

            func TestX(t *testing.T) {{
                var c Closer = b.Conn{{}}
                t.Log("TestX " + c.Close())
            }}
            """,
    }
    if direct:  # the plain call path: a library file and a constrained test file each calling c.Close() on a b.Conn
        files[f"a/{library[0]}_direct.go"] = f"""
            package a

            import "{M}/b"

            func Direct(c b.Conn) string {{ return c.Close() }}
            """
        files["a/m_linux_test.go"] = f"""
            package a

            import (
                "testing"

                "{M}/b"
            )

            func TestDirect(t *testing.T) {{
                c := b.Conn{{}}
                t.Log("TestDirect " + c.Close())
            }}
            """
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source))
    return root


@pytest.mark.parametrize("library", ORDERS.values(), ids=ORDERS.keys())
def test_a_library_interface_call_gets_no_implements_edge_to_a_method_a_test_file_overrides(tmp_path, library):
    # go test ./...: TestX Base.Close (a's test binary), TestUse Conn.Close (b's test binary, the one build where a Conn reaches Use)
    graph = build_graph(poisoned(tmp_path, library), GO)
    implements = sorted(f"{e.caller.removeprefix(M + '/')}>{e.callee.removeprefix(M + '/')}" for e in graph.edges if e.kind == "implements")
    assert implements == [], "Use is called with a Conn in b's tests, which run Conn.Close, and Base.Close nowhere that Use is reached with a Conn"
    assert callees(graph, "b_test:TestUse") == ["a:Use"]


@pytest.mark.parametrize("library", ORDERS.values(), ids=ORDERS.keys())
def test_a_direct_call_is_answered_per_file_whichever_file_is_read_first(tmp_path, library):
    graph = build_graph(poisoned(tmp_path, library, direct=True), GO)
    assert callees(graph, "a:TestDirect") == ["b:Base.Close"], "a's test binary links b without b's tests"
    assert callees(graph, "a:Direct") == [], "a library file is in b's test build too, where Conn.Close runs"
    assert len(left_open(graph, "a:Direct", "c.Close")) == 1


def test_go_answers_the_poisoned_module_per_build(tmp_path):
    go = shutil.which("go")
    if go is None:
        pytest.skip("the go toolchain is not on PATH")
    root = poisoned(tmp_path, "z.go", direct=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "GOCACHE": str(root / ".gocache"), "GOPATH": str(root / ".gopath"), "GOPROXY": "off", "GOENV": "off", "GOFLAGS": "", "GOWORK": "off", "CGO_ENABLED": "0"}
    out = subprocess.run([go, "test", "-count=1", "-json", "./..."], cwd=root, env=env, capture_output=True, text=True, check=True).stdout
    logged = {}
    for line in out.splitlines():
        event = json.loads(line)
        if event.get("Action") == "output" and event.get("Test") and event["Output"].rstrip().endswith(("Base.Close", "Conn.Close")):
            logged[event["Test"]] = event["Output"].rstrip().rsplit(" ", 1)[-1]
    assert logged == {"TestX": "Base.Close", "TestDirect": "Base.Close", "TestUse": "Conn.Close"}
