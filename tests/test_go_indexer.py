"""The Go front end (ripple/go_indexer.py): what is an edge, and what stays unresolved with which reason.

Every rule of the minimal model is pinned in both directions: the edge when the source proves it, the unresolved
call (with its reason) or the external count when it does not. The programs are small and are only parsed, never
compiled; bench/fixtures/go/shop is the compiled one (tests/test_go_golden.py).

These need tree-sitter-go (pip install 'ripple-code[go]') and are skipped without it.
"""

import importlib.util
import textwrap

import pytest

from ripple.indexer import build_graph
from ripple.profile import GO

HAS_GO = importlib.util.find_spec("tree_sitter_go") is not None and importlib.util.find_spec("tree_sitter") is not None
pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

M = "ex.com/m"


def go_repo(tmp_path, files, module=M):
    """Write the files (a go.mod for `module` is added unless one is given) and return the repo directory."""
    files = {"go.mod": f"module {module}\n\ngo 1.22\n", **files} if module and "go.mod" not in files else files
    for rel, source in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source))
    return tmp_path


def index(tmp_path, files, module=M):
    return build_graph(go_repo(tmp_path, files, module), GO)


def edges(graph, prefix=f"{M}:"):
    """The edges as sorted `caller>callee` strings, the common id prefix cut off."""
    return sorted(f"{e.caller.removeprefix(prefix)}>{e.callee.removeprefix(prefix)}" for e in graph.edges)


def unresolved(graph, prefix=f"{M}:"):
    return sorted((u.caller.removeprefix(prefix), u.text) for u in graph.unresolved)


def reasons(graph, text):
    return [u.reason for u in graph.unresolved if u.text == text]


def times(n, edge):
    return [edge] * n


# ---- one root package in one file: each rule, both directions -------------------------------------

PROVEN = {
    "a same-package function": ("func a() {}\nfunc b() { a() }\n", ["b>a"], []),
    "a method on a receiver": ("type T struct{}\nfunc (T) M() {}\nfunc (t *T) N() { t.M() }\n", ["T.N>T.M"], []),
    "a method on a parameter": ("type T struct{}\nfunc (T) M() {}\nfunc f(t *T) { t.M() }\n", ["f>T.M"], []),
    "a method on a named result": ("type T struct{}\nfunc (T) M() {}\nfunc f() (t T) { t.M(); return }\n", ["f>T.M"], []),
    "a declared, composite, address, new and constructor typed variable": (
        """
        type T struct{}
        func (T) M() {}
        func NewT() *T { return &T{} }
        func NewTE() (*T, error) { return &T{}, nil }
        func f() {
            var a T
            a.M()
            b := T{}
            b.M()
            c := &T{}
            c.M()
            d := new(T)
            d.M()
            e := NewT()
            e.M()
            g, err := NewTE()
            g.M()
            _ = err
        }
        """,
        ["f>NewT", "f>NewTE", *times(6, "f>T.M")],
        [],
    ),
    "a struct field, an element of a field, a map value and an element": (
        """
        type T struct{}
        func (T) M() {}
        type U struct {
            t  T
            ts []T
            m  map[string]*T
        }
        func f(u *U, xs []T, m map[string]T) {
            u.t.M()
            u.ts[0].M()
            u.m["k"].M()
            xs[1].M()
            m["a"].M()
        }
        """,
        times(5, "f>T.M"),
        [],
    ),
    "a range variable over a slice, a map and a named slice type": (
        """
        type T struct{}
        func (T) M() {}
        type Ts []T
        func f(xs []T, m map[string]T, ts Ts) {
            for _, x := range xs { x.M() }
            for _, v := range m { v.M() }
            for _, t := range ts { t.M() }
        }
        """,
        times(3, "f>T.M"),
        [],
    ),
    "a channel receive and a select receive": (
        """
        type T struct{}
        func (T) M() {}
        func f(ch chan T) {
            x := <-ch
            x.M()
            select {
            case y, ok := <-ch:
                y.M()
                _ = ok
            }
        }
        """,
        times(2, "f>T.M"),
        [],
    ),
    "a type assertion, its comma-ok form and a one-type switch case": (
        """
        type T struct{}
        func (T) M() {}
        func f(i any) {
            a := i.(T)
            a.M()
            b, ok := i.(*T)
            b.M()
            _ = ok
            switch s := i.(type) {
            case T:
                s.M()
            }
        }
        """,
        times(3, "f>T.M"),
        [],
    ),
    "a conversion, a method expression and a pointer method expression": (
        """
        type T struct{}
        func (T) M() {}
        func f(x int) {
            T(x).M()
            T.M(T{})
            (*T).M(nil)
        }
        """,
        times(3, "f>T.M"),
        [],
    ),
    "a method promoted from an embedded struct, at depth 1 and 2": (
        """
        type A struct{}
        func (A) M() {}
        type B struct{ A }
        type C struct{ *B }
        func f(b *B, c *C) { b.M(); c.M() }
        """,
        times(2, "f>A.M"),
        [],
    ),
    "the shallowest embedded type wins": (
        """
        type A struct{}
        func (A) M() {}
        type D struct{}
        func (D) M() {}
        type B struct{ A }
        type C struct {
            B
            D
        }
        func f(c *C) { c.M() }
        """,
        ["f>D.M"],
        [],
    ),
    "go and defer run their call": (
        "func a() {}\nfunc b() { go a(); defer a() }\n",
        ["b>a", "b>a"],
        [],
    ),
    "a function literal's calls belong to the function around it": (
        """
        func a() {}
        func b() {
            f := func() { a() }
            f()
            func() { a() }()
        }
        """,
        ["b>a", "b>a"],
        [("b", "f")],
    ),
    "a generic function, inferred or with one or two explicit type arguments": (
        """
        func G[T any](x T) T { return x }
        func H[A, B any](a A, b B) {}
        func f() {
            G(1)
            G[int](1)
            H[int, string](1, "a")
        }
        """,
        times(3, "f>G")[:2] + ["f>H"],
        [],
    ),
    "a generic type's method": (
        """
        type S[T any] struct{ xs []T }
        func (s *S[T]) Push(x T) { s.xs = append(s.xs, x) }
        func f() { s := &S[int]{}; s.Push(1) }
        """,
        ["f>S.Push"],
        [],
    ),
    "a package variable initialiser is called by the package": (
        "func a() int { return 1 }\nvar x = a()\nvar y = func() int { return a() }()\n",
        [f"{M}>a", f"{M}>a"],
        [],
    ),
    "a type alias to a repo type": (
        "type T struct{}\nfunc (T) M() {}\ntype A = T\nfunc f(a A) { a.M() }\n",
        ["f>T.M"],
        [],
    ),
    "init functions call and are called by nobody": (
        "func a() {}\nfunc init() { a() }\nfunc init() { a() }\n",
        ["init#2>a", "init>a"],
        [],
    ),
    "a method of a type defined from another repo type does not inherit its methods but its fields": (
        "type T struct{ f func() }\ntype U T\nfunc (U) N() {}\nfunc g(u U) { u.N() }\n",
        ["g>U.N"],
        [],
    ),
}


@pytest.mark.parametrize("name", PROVEN)
def test_the_source_proves_these_edges(tmp_path, name):
    source, want, unresolved_calls = PROVEN[name]
    graph = index(tmp_path, {"a.go": "package m\n" + textwrap.dedent(source)})
    assert edges(graph) == sorted(want), name
    assert [text for _, text in unresolved(graph)] == [text for _, text in unresolved_calls], name


# Every one of these must stay unresolved, with its reason, and make no edge to the repo.
UNRESOLVED = {
    "an interface method call names the interface": (
        "type I interface{ M() }\ntype T struct{}\nfunc (T) M() {}\nfunc f(i I) { i.M() }\n",
        "i.M",
        "interface ex.com/m:I",
    ),
    "an interface embedded in an interface still names the one that declares the method": (
        "type I interface{ M() }\ntype J interface{ I }\nfunc f(j J) { j.M() }\n",
        "j.M",
        "interface ex.com/m:J",
    ),
    "an interface embedded in a struct": (
        "type I interface{ M() }\ntype S struct{ I }\nfunc f(s *S) { s.M() }\n",
        "s.M",
        "interface ex.com/m:I",
    ),
    ":= in a function's own block reuses a parameter, so it keeps the declared interface type": (
        """
        type I interface{ M() }
        type T struct{}
        func (T) M() {}
        func pick() (*T, error) { return nil, nil }
        func f(i I) {
            i, err := pick()
            i.M()
            _ = err
        }
        """,
        "i.M",
        "interface ex.com/m:I",
    ),
    ":= in the same block reuses a variable declared there": (
        """
        type I interface{ M() }
        type T struct{}
        func (T) M() {}
        func f() {
            var i I = T{}
            i, ok := i.(T)
            i.M()
            _ = ok
        }
        """,
        "i.M",
        "interface ex.com/m:I",
    ),
    "a parameter holding a function": ("func f(g func()) { g() }\n", "g", "parameter holding a function value"),
    "a local holding a function": ("func f() { g := func() {}; g() }\n", "g", "local holding a function value"),
    "a field holding a function": ("type T struct{ f func() }\nfunc g(t *T) { t.f() }\n", "t.f", "struct field holding a function value"),
    "a package variable holding a function": ("var h = func() {}\nfunc g() { h() }\n", "h", "package-level variable holding a function value"),
    "a function returned by a call": ("func mk() func() { return nil }\nfunc g() { mk()() }\n", "mk()", "function returned by a call"),
    "a map of functions, called by index": ("var hs = map[string]func(){}\nfunc g(k string) { hs[k]() }\n", "hs[k]", "looked up by index"),
    "a slice of functions, called by a literal index": ("var hs []func()\nfunc g() { hs[0]() }\n", "hs[0]", "looked up by index"),
    "a type parameter's method": (
        "func f[T interface{ M() }](x T) { x.M() }\n",
        "x.M",
        "type parameter",
    ),
    "a method value": (
        "type T struct{}\nfunc (T) M() {}\nfunc f(t T) { g := t.M; g() }\n",
        "g",
        "local holding a function value",
    ),
    "a value whose type is not written": (
        "func f(x Missing) { x.M() }\n",
        "x.M",
        "cannot tell",
    ),
    "an outside type that a repo type also has a method of (it may be an interface)": (
        'import "io"\ntype T struct{}\nfunc (T) Close() error { return nil }\nfunc f(c io.Closer) { c.Close() }\n',
        "c.Close",
        "may be an interface",
    ),
    "the error interface when a repo type declares Error": (
        "type E struct{}\nfunc (E) Error() string { return \"\" }\nfunc f(err error) { err.Error() }\n",
        "err.Error",
        "may be an interface",
    ),
    "a name no scope declares": ("func f() { x.M() }\n", "x.M", "no scope ripple read declares"),
    "a bare name that is declared nowhere": ("func f() { ghost() }\n", "ghost", "not declared in the files"),
    "reflection is never external": (
        'import "reflect"\nfunc f(v reflect.Value) { v.Call(nil) }\n',
        "v.Call",
        "Call or CallSlice",
    ),
    "a local type's value": (
        "type T struct{}\nfunc (T) M() {}\nfunc f() { type L struct{ T }; var l L; l.M() }\n",
        "l.M",
        "cannot tell",
    ),
    "a call inside a type switch case of two types, where the value keeps the type of the switch": (
        """
        type T struct{}
        func (T) M() {}
        func f(i any) {
            switch s := i.(type) {
            case T, *T:
                s.M()
            }
        }
        """,
        "s.M",
        "has no member table for",
    ),
    "a name hidden by a local shadowing a package-level function": (
        "func a() {}\nfunc f(a func()) { a() }\n",
        "a",
        "parameter holding a function value",
    ),
}


@pytest.mark.parametrize("name", UNRESOLVED)
def test_this_stays_unresolved_with_its_reason(tmp_path, name):
    source, text, fragment = UNRESOLVED[name]
    graph = index(tmp_path, {"a.go": "package m\n" + textwrap.dedent(source)})
    found = reasons(graph, text)
    assert found, (name, unresolved(graph))
    assert all(fragment in reason for reason in found), (name, found)
    callers = {e.caller for e in graph.edges}
    assert callers <= {f"{M}:pick", f"{M}:mk"} or not [e for e in graph.edges if e.callee.endswith((".M", ".Close", ".Error"))], (name, edges(graph))


def test_a_universe_builtin_is_external_and_never_an_edge(tmp_path):
    graph = index(tmp_path, {"a.go": "package m\nfunc f(xs []int) int { xs = append(xs, 1); panic(len(xs)) }\n"})
    assert graph.edges == [] and graph.unresolved == []
    assert graph.external_reasons == {"a Go builtin": 3}
    assert graph.external_calls == 3


def test_a_conversion_is_not_a_call_at_all(tmp_path):
    source = """
        package m
        type T struct{}
        func (T) M() {}
        type Cents int64
        func f(x int, s string) {
            _ = Cents(x)
            _ = (*T)(nil)
            _ = []byte(s)
            _ = T{}
        }
    """
    graph = index(tmp_path, {"a.go": source})
    assert graph.edges == [] and graph.unresolved == [] and graph.external_calls == 0


def test_a_standard_library_struct_method_is_external_and_an_interface_is_not(tmp_path):
    source = """
        package m
        import (
            "io"
            "strings"
            "sync"
        )
        func f(r io.Reader) {
            var sb strings.Builder
            sb.WriteString("x")
            var mu sync.Mutex
            mu.Lock()
            r.Read(nil)
        }
    """
    graph = index(tmp_path, {"a.go": source})
    assert graph.edges == [] and graph.unresolved == []
    assert graph.external_reasons == {
        "a method of a standard library type that is not an interface": 2,
        "a method of a standard library interface which no repo type declares": 1,
    }


def test_a_function_of_the_standard_library_is_external(tmp_path):
    graph = index(tmp_path, {"a.go": 'package m\nimport "fmt"\nfunc f() { fmt.Println("x") }\n'})
    assert graph.edges == [] and graph.unresolved == []
    assert graph.external_reasons == {"a function declared by a standard library package": 1}


def test_a_method_promoted_only_from_an_outside_embedded_type_is_external(tmp_path):
    source = 'package m\nimport "bytes"\ntype S struct{ *bytes.Buffer }\nfunc f(s *S) int { return s.Len() }\n'
    graph = index(tmp_path, {"a.go": source})
    assert graph.edges == [] and graph.unresolved == []
    assert graph.external_reasons == {"a method promoted from an embedded standard library type": 1}


def test_an_outside_embedded_type_at_a_shallower_depth_may_provide_the_method_first(tmp_path):
    source = """
        package m
        import "bytes"
        type Base struct{}
        func (Base) Reset() {}
        type Mid struct{ Base }
        type Top struct {
            Mid
            *bytes.Buffer
        }
        func f(t *Top, m *Mid) { t.Reset(); m.Reset() }
    """
    graph = index(tmp_path, {"a.go": source})
    assert edges(graph) == ["f>Base.Reset"]  # only m.Reset: Top.Reset may be bytes.Buffer's
    assert "shallower depth" in reasons(graph, "t.Reset")[0]


def test_a_local_that_shadows_an_imported_package_name_is_the_local(tmp_path):
    source = """
        package m
        import "ex.com/m/util"
        type L struct{}
        func (L) Do() {}
        func f() {
            util := L{}
            util.Do()
        }
        func g() { util.Do() }
    """
    graph = index(tmp_path, {"a.go": source, "util/u.go": "package util\nfunc Do() {}\n"})
    assert edges(graph) == ["f>L.Do", "g>ex.com/m/util:Do"]


def test_an_import_alias_and_a_package_named_unlike_its_path(tmp_path):
    files = {
        "a.go": 'package m\nimport (\n\tu "ex.com/m/util"\n\t"ex.com/m/lib/v2"\n)\nfunc f() { u.Do(); lib.Run() }\n',
        "util/u.go": "package util\nfunc Do() {}\n",
        "lib/v2/l.go": "package lib\nfunc Run() {}\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == ["f>ex.com/m/lib/v2:Run", "f>ex.com/m/util:Do"]


def test_a_dot_import_of_a_repo_package_binds_its_names_and_one_from_outside_is_external(tmp_path):
    files = {
        "a.go": 'package m\nimport . "ex.com/m/util"\nfunc f() { Do(); T{}.M() }\n',
        "b.go": 'package m\nimport . "strings"\nfunc g() { ToUpper("a") }\n',
        "util/u.go": "package util\ntype T struct{}\nfunc (T) M() {}\nfunc Do() {}\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == ["f>ex.com/m/util:Do", "f>ex.com/m/util:T.M"]
    assert graph.external_reasons == {"a function of a standard library package, named by a dot import": 1}


def test_cgo_calls_are_unresolved_never_external(tmp_path):
    graph = index(tmp_path, {"a.go": 'package m\n/*\n#include <stdio.h>\n*/\nimport "C"\nfunc f() { C.puts(C.CString("x")) }\n'})
    assert graph.edges == [] and graph.external_reasons == {}
    assert sorted((u.text, u.reason.split(",")[0]) for u in graph.unresolved) == [("C.CString", "cgo: a C function"), ("C.puts", "cgo: a C function")]
    assert all("//export" in u.reason for u in graph.unresolved)


# ---- what leaves the repo is only what the standard library's source proves ---------------------------------


def test_a_package_variable_or_a_function_typed_field_of_an_outside_package_is_never_external(tmp_path):
    source = """
        package m
        import (
            "flag"
            "net"
            "net/http"
        )
        func f(t *http.Transport, d *net.Dialer, s *http.Server) {
            flag.Usage()
            t.DialContext(nil, "", "")
            d.Control("", "", nil)
            s.ConnState(nil, 0)
            flag.Parse()
            t.CloseIdleConnections()
        }
    """
    graph = index(tmp_path, {"a.go": source})
    assert graph.edges == []
    assert graph.external_reasons == {
        "a function declared by a standard library package": 1,
        "a method of a standard library type that is not an interface": 1,
    }
    found = {u.text: u.reason for u in graph.unresolved}
    assert set(found) == {"flag.Usage", "t.DialContext", "d.Control", "s.ConnState"}
    assert "not a function declaration" in found["flag.Usage"]
    assert all("function value" in found[name] or "holds a function" in found[name] or "holding a function" in found[name] for name in found if name != "flag.Usage")


def test_a_member_of_a_package_or_type_that_is_not_the_standard_library_is_unresolved_never_external(tmp_path):
    source = """
        package m
        import "github.com/spf13/pflag"
        func f(fs *pflag.FlagSet) {
            pflag.Parse()
            fs.Usage()
            pflag.NewFlagSet("x", 0).Bool("b", false, "")
        }
    """
    graph = index(tmp_path, {"a.go": source})
    assert graph.edges == [] and graph.external_reasons == {}
    assert sorted(u.text for u in graph.unresolved) == ["fs.Usage", "pflag.NewFlagSet", "pflag.NewFlagSet(\"x\", 0).Bool", "pflag.Parse"]


def test_a_conversion_to_a_standard_library_type_is_no_call(tmp_path):
    source = 'package m\nimport (\n"net/http"\n"time"\n)\nfunc f(h func(http.ResponseWriter, *http.Request)) { _ = http.HandlerFunc(h); _ = time.Duration(5) }\n'
    graph = index(tmp_path, {"a.go": source})
    assert graph.edges == [] and graph.unresolved == [] and graph.external_reasons == {}


def test_the_standard_library_table_says_what_each_name_is():
    from ripple.go_indexer import outside_type, stdlib_funcs

    assert {"Println", "Sprintf"} <= stdlib_funcs("fmt") and "Usage" not in stdlib_funcs("flag") and "Parse" in stdlib_funcs("flag")
    assert stdlib_funcs("github.com/spf13/pflag") is None and "Sizeof" in stdlib_funcs("unsafe")
    kind, methods, fields = outside_type("net/http.Transport")
    assert kind == "s" and {"DialContext", "Proxy", "DialTLS"} <= fields and "CloseIdleConnections" in methods and "DialContext" not in methods
    assert outside_type("io.Reader")[0] == "i" and outside_type("io.Reader")[1] == {"Read"}
    assert outside_type("flag.FlagSet")[2] == {"Usage"} and outside_type("net/http.Get") is None and outside_type("any") is None
    assert outside_type("error") == ("i", {"Error"}, frozenset())


def test_go_files_below_the_directories_the_go_tool_skips_are_listed_as_skipped(tmp_path):
    from ripple.indexer import walk_repo

    repo = go_repo(
        tmp_path,
        {
            "a.go": "package m\n",
            "_examples/rest/main.go": "package main\n",
            "sub/vendor/x/y.go": "package y\n",
            "sub/testdata/t.go": "package t\n",
            "_ignored.go": "package m\n",
            "empty/vendor/readme.txt": "no go here\n",
            ".hidden/h.go": "package h\n",
        },
    )
    files, skipped = walk_repo(repo, GO)
    assert [f.relative_to(repo).as_posix() for f in files] == ["a.go"]
    assert skipped == ["_examples", "sub/testdata", "sub/vendor"]
    graph = build_graph(repo, GO)
    assert graph.skipped_dirs == ["_examples", "sub/testdata", "sub/vendor"]


def test_the_skipped_directories_of_a_python_repo_are_what_they_were(tmp_path):
    from ripple.indexer import walk_repo
    from ripple.profile import PYTHON

    for rel in ("a.py", "vendor/v.py", "testdata/t.py", "_x/u.py", "node_modules/n.py", "build/b.py", "pkg/vendor/w.py"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
    files, skipped = walk_repo(tmp_path, PYTHON)
    assert sorted(f.relative_to(tmp_path).as_posix() for f in files) == ["_x/u.py", "a.py", "pkg/vendor/w.py", "testdata/t.py", "vendor/v.py"]
    assert skipped == ["build", "node_modules"]


# ---- the lexical containment of a parse error (no layout, no comment, no string decides it) ------------------


@pytest.mark.parametrize(
    "block, closes",
    [
        ("{ }", True),
        ("{ a { b } c }", True),
        ("{ x := \"}\"; y := `}`; z := '}' }", True),
        ("{ /* } */ a } // }", True),  # a brace in a comment is no brace, before or after the block's own
        ("{ } }", False),
        ("{ { }", False),
        ("{ a }\n\tfunc (T) M() {}", False),
        ("{ // }\n}", True),
        ("{ '\\'' }", True),
        ("{ \"\\\"}\" }", True),
    ],
)
def test_a_block_is_closed_by_its_last_brace_and_by_no_earlier_one(block, closes):
    from ripple.go_indexer import closes_only_at_end

    assert closes_only_at_end(block.encode()) is closes


# ---- the same name declared more than once (build constraints are never evaluated) -----------------


def test_a_function_defined_once_per_build_has_an_edge_to_every_variant(tmp_path):
    files = {
        "os_linux.go": "package m\nfunc platform() string { return \"l\" }\n",
        "os_windows.go": "package m\nfunc platform() string { return \"w\" }\n",
        "a.go": "package m\nfunc f() string { return platform() }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == ["f>platform", "f>platform#2"]
    assert {s.id for s in graph.symbols.values() if s.kind == "function"} >= {f"{M}:platform", f"{M}:platform#2"}


def test_a_method_defined_once_per_build_has_an_edge_to_every_variant(tmp_path):
    files = {
        "t.go": "package m\ntype T struct{}\n",
        "a.go": "package m\nfunc (T) M() {}\n",
        "b.go": "package m\nfunc (T) M() {}\n",
        "c.go": "package m\nfunc f(t T) { t.M() }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == ["f>T.M", "f>T.M#2"]


def test_a_variable_declared_differently_in_two_files_is_not_typed(tmp_path):
    """Declared as an interface under one build and as a struct under another: an edge to the struct's method would be wrong."""
    files = {
        "a.go": "//go:build !fast\n\npackage m\nvar Strategy I = T{}\n",
        "b.go": "//go:build fast\n\npackage m\nvar Strategy = T{}\n",
        "t.go": "package m\ntype I interface{ M() }\ntype T struct{}\nfunc (T) M() {}\nfunc f() { Strategy.M() }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == []
    assert "declared differently in several files" in reasons(graph, "Strategy.M")[0]


def test_a_variable_declared_identically_in_two_files_is_typed(tmp_path):
    files = {
        "a.go": "//go:build linux\n\npackage m\nvar S = T{}\n",
        "b.go": "//go:build !linux\n\npackage m\nvar S = T{}\n",
        "t.go": "package m\ntype T struct{}\nfunc (T) M() {}\nfunc f() { S.M() }\n",
    }
    assert edges(index(tmp_path, files)) == ["f>T.M"]


def test_a_type_declared_twice_is_not_followed_but_a_method_found_by_name_is(tmp_path):
    files = {
        "a.go": "//go:build !b\n\npackage m\ntype G struct{ hook func() }\nfunc (g *G) Extra() {}\n",
        "b.go": "//go:build b\n\npackage m\ntype G struct{ n int }\n",
        "c.go": "package m\nfunc (g *G) Ping() {}\nfunc f(g *G) { g.Ping(); g.hook() }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == ["f>G.Ping"]
    assert "type is declared more than once" in reasons(graph, "g.hook")[0]


def test_a_name_declared_as_a_function_and_as_a_variable_is_unresolved(tmp_path):
    files = {
        "a.go": "//go:build x\n\npackage m\nfunc F() bool { return true }\n",
        "b.go": "//go:build !x\n\npackage m\nvar F = func() bool { return false }\n",
        "c.go": "package m\nfunc g() bool { return F() }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == []
    assert "declared more than once" in reasons(graph, "F")[0]


# ---- ids, packages and files -----------------------------------------------------------------------


def test_ids_are_import_paths_and_every_file_of_a_directory_is_one_package(tmp_path):
    files = {
        "a.go": "package m\nfunc A() {}\n",
        "b.go": "package m\nfunc B() { A() }\ntype T struct{}\nfunc (*T) M() {}\n",
        "sub/s.go": "package sub\nfunc S() {}\n",
    }
    graph = index(tmp_path, files)
    assert {s.id: s.kind for s in graph.symbols.values()} == {
        M: "module",
        f"{M}:A": "function",
        f"{M}:B": "function",
        f"{M}:T": "class",
        f"{M}:T.M": "method",
        f"{M}/sub": "module",
        f"{M}/sub:S": "function",
    }
    assert graph.modules == {"a.go": M, "b.go": M, "sub/s.go": f"{M}/sub"}
    assert edges(graph) == ["B>A"]


def test_symbols_know_their_file_and_lines(tmp_path):
    graph = index(tmp_path, {"a.go": "package m\n\nfunc A() {\n\tprintln()\n}\n"})
    symbol = graph.symbols[f"{M}:A"]
    assert (symbol.file, symbol.line, symbol.end_line) == ("a.go", 3, 5)


def test_an_edge_is_at_the_line_of_its_call(tmp_path):
    graph = index(tmp_path, {"a.go": "package m\nfunc a() {}\nfunc b() {\n\tx := 1\n\ta()\n}\n"})
    assert [(e.file, e.line) for e in graph.edges] == [("a.go", 5)]


def test_package_main_in_several_directories_has_one_id_each(tmp_path):
    files = {"cmd/a/main.go": "package main\nfunc main() {}\n", "cmd/b/main.go": "package main\nfunc main() {}\n"}
    graph = index(tmp_path, files)
    assert {f"{M}/cmd/a:main", f"{M}/cmd/b:main"} <= set(graph.symbols)


def test_an_external_test_package_is_its_own_package_and_an_in_package_test_joins_the_package(tmp_path):
    files = {
        "a.go": "package m\nfunc A() {}\n",
        "a_test.go": "package m\nimport \"testing\"\nfunc TestInside(t *testing.T) { A() }\n",
        "x_test.go": 'package m_test\nimport (\n\t"testing"\n\t"ex.com/m"\n)\nfunc TestOutside(t *testing.T) { m.A() }\n',
    }
    graph = index(tmp_path, files)
    assert graph.modules == {"a.go": M, "a_test.go": M, "x_test.go": f"{M}_test"}
    assert edges(graph) == ["TestInside>A", f"{M}_test:TestOutside>A"]  # the external test package calls the package by its import


def test_the_package_name_an_import_binds_is_the_one_its_clause_says(tmp_path):
    files = {
        "a.go": 'package m\nimport "ex.com/m/go-util"\nfunc f() { util.Do() }\n',
        "go-util/u.go": "package util\nfunc Do() {}\n",
    }
    assert edges(index(tmp_path, files)) == ["f>ex.com/m/go-util:Do"]


def test_files_the_go_tool_does_not_build_are_not_read(tmp_path):
    files = {
        "a.go": "package m\nfunc A() {}\n",
        "vendor/dep/d.go": "package dep\nfunc D() {}\n",
        "x/vendor/dep/d.go": "package dep\nfunc D() {}\n",
        "testdata/t.go": "package t\nfunc T() {}\n",
        "_scratch/s.go": "package s\nfunc S() {}\n",
        "gen.go": "//go:build ignore\n\npackage main\nfunc main() {}\n",
        "_hidden.go": "package m\nfunc Hidden() {}\n",
        ".dot.go": "package m\nfunc Dot() {}\n",
    }
    graph = index(tmp_path, files)
    assert set(graph.modules) == {"a.go"}
    assert set(graph.symbols) == {M, f"{M}:A"}


def test_a_repo_that_sits_below_a_directory_named_vendor_is_still_read(tmp_path):
    repo = tmp_path / "vendor" / "checkout"
    graph = index(repo, {"a.go": "package m\nfunc A() {}\nfunc B() { A() }\n"})
    assert edges(graph) == ["B>A"]


def test_a_generated_file_is_read_and_called_into(tmp_path):
    files = {
        "gen.go": "// Code generated by tool. DO NOT EDIT.\n\npackage m\nfunc Gen() {}\n",
        "a.go": "package m\nfunc A() { Gen() }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == ["A>Gen"] and set(graph.modules) == {"gen.go", "a.go"}


def test_a_file_outside_any_module_has_its_directory_for_an_id_and_proves_no_import(tmp_path):
    files = {"p/a.go": 'package p\nimport "ex.com/m/q"\nfunc A() { q.Q(); B() }\nfunc B() {}\n', "q/q.go": "package q\nfunc Q() {}\n"}
    graph = index(tmp_path, files, module=None)
    assert edges(graph, prefix="") == ["p:A>p:B"]
    assert graph.modules == {"p/a.go": "p", "q/q.go": "q"}


def test_two_directories_with_one_import_path_do_not_share_ids(tmp_path):
    files = {
        "a/go.mod": "module ex.com/dup\n",
        "a/x.go": "package dup\nfunc F() {}\n",
        "b/go.mod": "module ex.com/dup\n",
        "b/x.go": "package dup\nfunc F() {}\n",
    }
    graph = index(tmp_path, files, module=None)
    assert len({m for m in graph.modules.values()}) == 2


def test_a_package_clause_that_matches_neither_package_of_its_directory_is_left_out_and_listed(tmp_path):
    files = {"a.go": "package m\nfunc A() {}\n", "b.go": "package m\nfunc B() {}\n", "odd.go": "package other\nfunc O() {}\n"}
    graph = index(tmp_path, files)
    assert "odd.go" in graph.parse_errors and "odd.go" not in graph.modules


# ---- a file tree-sitter cannot read completely ---------------------------------------------------------


BROKEN = """
    package m
    func a() {}
    func intact() { a() }
    func broken(xs []int) *int { a(); return new(int(len(xs))) }
"""


def test_a_function_with_code_tree_sitter_cannot_read_keeps_its_symbol_and_has_unresolved_calls(tmp_path):
    graph = index(tmp_path, {"a.go": BROKEN})
    assert graph.parse_errors == ["a.go"]
    assert f"{M}:broken" in graph.symbols
    assert edges(graph) == ["intact>a"]  # the intact function of the same file is read as usual
    assert unresolved(graph) and {c for c, _ in unresolved(graph)} == {"broken"}
    assert all("tree-sitter could not read" in u.reason for u in graph.unresolved)


def test_a_file_that_cannot_be_read_costs_that_file_only(tmp_path):
    repo = go_repo(tmp_path, {"a.go": "package m\nfunc A() {}\n", "b.go": "package m\nfunc B() { A() }\n"})
    (repo / "b.go").write_bytes(b"\xff\xfe package")  # not valid UTF-8, no package clause tree-sitter can find
    graph = build_graph(repo, GO)
    assert "b.go" in graph.parse_errors and set(graph.modules) == {"a.go"}


# ---- what a review of the first Go front end found, one case each --------------------------------------
# (tests/test_go_probes.py runs the same shapes against the compiler's own answers)

ALIAS = """
    package m
    type Base struct{}
    func (Base) M() {}
    type B struct{ Base }
    type A = B
    func (A) M() {}
    func f() { var b B; b.M(); var a A; a.M() }
"""


def test_a_method_declared_on_an_alias_is_a_method_of_its_target_and_keeps_the_alias_name(tmp_path):
    graph = index(tmp_path, {"a.go": ALIAS})
    assert edges(graph) == ["f>A.M", "f>A.M"]  # the compiler's id, and not the promoted Base.M it shadows
    assert f"{M}:A.M" in graph.symbols


HIDING = {
    "broken.go": """
        package m
        func g() uint64 { return 1 }
        var Counter = new(uint64(g()))
        func (T) M() {}
        func len(x []int) int { return 0 }
    """,
    "use.go": """
        package m
        type Base struct{}
        func (Base) M() {}
        type T struct{ Base }
        func f() {
            var t T
            t.M()
            _ = len([]int{1})
        }
    """,
}


def test_a_package_level_declaration_tree_sitter_cannot_read_leaves_its_package_with_declarations_unknown(tmp_path):
    graph = index(tmp_path, HIDING)
    assert graph.parse_errors == ["broken.go"]
    assert edges(graph) == []  # not Base.M (T.M may be the swallowed one), and not the builtin len (a swallowed func len)
    assert all("broken.go" in r for r in reasons(graph, "t.M") + reasons(graph, "len"))
    assert graph.external_calls == 0


def test_a_name_found_directly_in_a_package_with_an_unreadable_file_is_still_proven(tmp_path):
    files = {**HIDING, "use.go": HIDING["use.go"].replace("t.M()", "t.Own()").replace("type T struct{ Base }", "type T struct{ Base }\n func (T) Own() {}")}
    assert edges(index(tmp_path, files)) == ["f>T.Own"]


def test_a_method_name_an_unreadable_file_may_declare_is_not_proven_to_stay_outside_the_repo(tmp_path):
    files = {
        "a/broken.go": "package a\nvar C = new(uint64(1))\nfunc (X) Close() {}\n",
        "b/b.go": 'package b\nimport "io"\nfunc f(c io.Closer) { c.Close() }\nfunc g(c io.Reader) { c.Read(nil) }\n',
    }
    graph = index(tmp_path, files)
    assert "a/broken.go" in reasons(graph, "c.Close")[0]
    assert graph.external_calls == 1  # c.Read: no repo type and no unreadable text declares a method of that name


def test_a_function_whose_body_swallows_the_declarations_after_it_is_not_a_contained_error(tmp_path):
    files = {
        "a.go": """
            package m
            type Base struct{}
            func (Base) M() {}
            type T struct{ Base }
            func broken() {
                a()

            func (T) M() {}
            func a() {}
            func f() { var t T; t.M() }
        """
    }
    assert "f>Base.M" not in edges(index(tmp_path, files))


def test_a_file_with_no_package_clause_may_hold_anything_of_its_directory(tmp_path):
    files = {
        "a.go": "package m\ntype Base struct{}\nfunc (Base) M() {}\ntype T struct{ Base }\nfunc f() { var t T; t.M() }\n",
        "b.go": "func (T) M() {}\n",
    }
    graph = index(tmp_path, files)
    assert "b.go" in graph.parse_errors and edges(graph) == []
    assert "b.go" in reasons(graph, "t.M")[0]


PA = "package pa\ntype C struct{}\nfunc (C) Do() {}\nfunc New() *C { return &C{} }\n"
VARIANT_IMPORTS = {
    "pa/pa.go": PA,
    "pb/pb.go": PA.replace("package pa", "package pb"),
    "x_linux.go": 'package m\nimport foo "ex.com/m/pa"\nvar X = foo.New()\nfunc Make() *foo.C { return foo.New() }\n',
    "x_windows.go": 'package m\nimport foo "ex.com/m/pb"\nvar X = foo.New()\nfunc Make() *foo.C { return foo.New() }\n',
    "c.go": "package m\nfunc f() {\n  X.Do()\n  Make().Do()\n}\n",
}


def test_a_package_var_or_result_written_alike_but_importing_differently_per_file_is_unresolved(tmp_path):
    graph = index(tmp_path, VARIANT_IMPORTS)
    assert not [e for e in graph.edges if e.callee.endswith("C.Do")]
    assert "declared differently in several files" in reasons(graph, "X.Do")[0]
    assert "different results" in reasons(graph, "Make().Do")[0]


def test_a_method_declared_only_in_some_builds_does_not_shadow_the_promoted_one(tmp_path):
    base = "package m\ntype Base struct{}\nfunc (Base) M() {}\nfunc (Base) K() {}\ntype T struct{ Base }\n"
    for name in ("x_linux.go", "x_test.go", "x.go"):
        files = {"a.go": base + "func f() { var t T; t.M(); t.K() }\n", name: "//go:build linux\n\npackage m\nfunc (T) M() {}\n" if name == "x.go" else "package m\nfunc (T) M() {}\n"}
        graph = index(tmp_path / name, files)
        assert edges(graph) == ["f>Base.K"], name
        assert "declared only in some builds" in reasons(graph, "t.M")[0], name


def test_a_method_declared_only_in_some_builds_is_proven_when_nothing_else_could_be_called(tmp_path):
    files = {
        "a.go": "package m\ntype T struct{}\nfunc f() { var t T; t.Only() }\n",
        "x_linux.go": "package m\nfunc (T) Only() {}\n",
        "x_windows.go": "package m\nfunc (T) Only() {}\n",
    }
    assert edges(index(tmp_path, files)) == ["f>T.Only", "f>T.Only#2"]


def test_a_method_in_one_declaration_of_a_type_declared_twice_is_not_proven(tmp_path):
    files = {
        "a.go": "package m\ntype Base struct{}\nfunc (Base) M() {}\nfunc f() { var t T; t.M() }\n",
        "t_linux.go": "package m\ntype T struct{}\nfunc (T) M() {}\n",
        "t_windows.go": "package m\ntype T struct{ Base }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == [] and reasons(graph, "t.M")


def test_the_function_typed_fields_of_the_standard_library_types_are_function_values_not_external_calls(tmp_path):
    files = {"a.go": 'package m\nimport (\n "flag"\n "os/exec"\n)\nfunc f(fs *flag.FlagSet, c *exec.Cmd) {\n fs.Usage()\n c.Cancel()\n _ = fs.Parse(nil)\n}\n'}
    graph = index(tmp_path, files)
    assert reasons(graph, "fs.Usage") == reasons(graph, "c.Cancel") == ["the callee is a struct field holding a function value"]
    assert graph.external_calls == 1  # fs.Parse is a method


def test_a_module_fixture_under_testdata_or_an_underscore_directory_is_not_read_whatever_its_go_mod_says(tmp_path):
    files = {
        "lib/lib.go": "package lib\nfunc Helper() {}\nfunc Use() { Helper() }\n",
        "lib/testdata/fixture/go.mod": f"module {M}\n",
        "lib/testdata/fixture/broken.go": "package lib\nfunc Helper() {}\nfunc Use() { Helper() }\n",
        "_tools/go.mod": "module ex.com/tools\n",
        "_tools/t.go": "package tools\nfunc T() {}\n",
        "x/.cache/go.mod": "module ex.com/cache\n",
        "x/.cache/c.go": "package cache\nfunc C() {}\n",
    }
    graph = index(tmp_path, files)
    assert set(graph.modules) == {"lib/lib.go"}
    assert edges(graph, prefix=f"{M}/lib:") == ["Use>Helper"]


# the four guards a mutation run found no test biting on

TWO_BUILDS = {
    "a.go": "package m\ntype A struct{}\ntype B struct{}\ntype S struct{ A; B }\nfunc f(s S) { s.M() }\n",
    "a_linux.go": "package m\nfunc (A) M() {}\n",
    "b_windows.go": "package m\nfunc (B) M() {}\n",
}


def test_two_embedded_types_that_each_have_the_method_in_another_build_are_two_candidates(tmp_path):
    graph = index(tmp_path, TWO_BUILDS)
    assert edges(graph) == [] and reasons(graph, "s.M") == ["two embedded types at one depth provide this name"]


def test_a_name_that_is_a_function_in_one_build_and_a_variable_in_another_has_no_type(tmp_path):
    files = {
        "a.go": "package m\ntype A struct{}\nfunc (A) Do() {}\ntype B struct{}\nfunc (B) Do() {}\nfunc f() { g := X; g().Do() }\n",
        "x_linux.go": "package m\nfunc X() *A { return &A{} }\n",
        "x_windows.go": "package m\nvar X = func() *B { return &B{} }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == []


def test_a_type_declared_in_a_function_shadows_the_package_type_of_its_name(tmp_path):
    files = {
        "a.go": """
            package m
            type A struct{}
            func (A) M() {}
            type B struct{}
            func (B) M() {}
            type T struct{ A }
            func f() { type T struct{ B }; var t T; t.M() }
            func g() { var t T; t.M() }
        """
    }
    assert edges(index(tmp_path, files)) == ["g>A.M"]


def test_variants_that_disagree_on_what_a_function_returns_give_the_call_no_type(tmp_path):
    files = {
        "a.go": "package m\ntype A struct{}\nfunc (A) Do() {}\ntype B struct{}\nfunc (B) Do() {}\nfunc f() { m := Make(); m.Do() }\n",
        "m_linux.go": "package m\nfunc Make() *A { return &A{} }\n",
        "m_windows.go": "package m\nfunc Make() *B { return &B{} }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == ["f>Make", "f>Make#2"] and "different results" in reasons(graph, "m.Do")[0]


def test_a_method_declared_in_a_test_file_is_proven_for_a_call_in_a_test_file(tmp_path):
    files = {
        "a.go": "package m\ntype Base struct{}\nfunc (Base) Close() {}\ntype Conn struct{ Base }\nfunc prod(c Conn) { c.Close() }\n",
        "a_test.go": "package m\nfunc (Conn) Close() {}\n",
        "b_test.go": "package m\nfunc check(c Conn) { c.Close() }\n",
    }
    graph = index(tmp_path, files)
    assert edges(graph) == ["check>Conn.Close"]  # a test build has both test files; the production call may be either
    assert "declared only in some builds" in reasons(graph, "c.Close")[0]
