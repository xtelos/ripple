"""A type that ONLY a .java file ripple did not read declares is never a library type.

The files in question are repo code that the walk does not hand over (generated sources under target/ and build/, package-info.java,
a file whose directory is not its package's, a file the parser read in part). javac compiles them with the rest, so `Maker.make()`,
`new Maker()`, a static import of Maker's members and `class Sub extends Maker` run THEIR code, whether Maker was reached by a
single-type import, a full name, a static import, an on-demand import or its package. Every place that decides "this type is not a
repo type, so it is a library type" asks ONE question, JavaResolver.unread_type(qualified name): may an unread file declare it? and a
verdict that rests on the answer is unresolved with that reason, never external.

The javac fact each case rests on is in test_javac_resolves_every_route_to_the_unread_class (compiled with javac 21 when one is on
PATH): `import q.gen.Maker;` and a call `Maker.make()` are `invokestatic q/gen/Maker.make`, `new Maker()` is `invokespecial
q/gen/Maker."<init>"`, and the same through `import static q.gen.Maker.make;`, `import static q.gen.Maker.*;`, `class Sub extends Maker`
(`invokespecial q/gen/Maker.run` for `super.run()`), a full name and an on-demand import, with Maker in
target/generated-sources/annotations/q/gen/Maker.java.

The invariant at the end lists every place the resolver builds an external verdict (read off the module's own syntax tree, so a new one
fails the test until it is classified), and runs a matrix of every route twice: with the type declared only by an unread file (no
external verdict may come out of it) and with the type supplied by a jar (each library-type verdict must come out of it, so the matrix
is known to reach the route).
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_java")

from ripple import java_resolver  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

MARKERS = {"pom.xml": "<project/>"}
GENERATED = "target/generated-sources/annotations/"

MAKER = (
    "package q.gen;\n"
    "public class Maker {\n"
    "    public static Maker make() { return new Maker(); }\n"
    '    public Maker() { System.out.println("repo code"); }\n'
    "    public void run() {}\n"
    "    public static class Builder { public static Builder of() { return new Builder(); } }\n"
    "}\n"
)
BOOM = "package q.gen;\npublic class Boom extends Exception { public void report() {} }\n"
HANDLER = "package q.gen;\npublic interface Handler { void handle(); static Handler none() { return () -> {}; } }\n"
LOCAL = "package p;\npublic class Local { public static Local of() { return new Local(); } public void go() {} }\n"
THING = "package javax.gen;\npublic class Thing { public static void make() {} }\n"

UNREAD = {
    GENERATED + "q/gen/Maker.java": MAKER,
    GENERATED + "q/gen/Boom.java": BOOM,
    GENERATED + "q/gen/Handler.java": HANDLER,
    GENERATED + "p/Local.java": LOCAL,
    GENERATED + "javax/gen/Thing.java": THING,
}

# One class per way a name can reach the type; each method is one site, and a route is named as the test names it.
USES = {
    "Single": (
        "package p;\nimport q.gen.Maker;\nimport q.gen.Boom;\n"
        "class Single {\n"
        "    Maker field;\n"
        "    void staticCall() { Maker.make(); }\n"
        "    void creation() { new Maker(); }\n"
        "    void param(Maker m) { m.run(); }\n"
        "    void fieldCall() { field.run(); }\n"
        "    void local() { Maker m = null; m.run(); }\n"
        "    void cast(Object o) { ((Maker) o).run(); }\n"
        "    void anonymous() { new Maker() { }; }\n"
        "    void caught() { try { throw new Boom(); } catch (Boom e) { e.report(); } }\n"
        "}\n"
    ),
    "Qualified": (
        "package p;\n"
        "class Qualified {\n"
        "    q.gen.Maker field;\n"
        "    void creation() { new q.gen.Maker(); }\n"
        "    void staticCall() { q.gen.Maker.make(); }\n"
        "    void param(q.gen.Maker m) { m.run(); }\n"
        "    void fieldCall() { field.run(); }\n"
        "}\n"
    ),
    "StaticSingle": "package p;\nimport static q.gen.Maker.make;\nclass StaticSingle { void go() { make(); } }\n",
    "StaticOnDemand": "package p;\nimport static q.gen.Maker.*;\nclass StaticOnDemand { void go() { make(); } }\n",
    "Sub": (
        "package p;\nimport q.gen.Maker;\n"
        "class Sub extends Maker {\n"
        "    Sub() { super(); }\n"
        "    @Override public void run() { super.run(); }\n"
        "    void viaSub() { Sub.make(); }\n"
        "    void unqualified() { make(); }\n"
        "}\n"
    ),
    "SubUser": "package p;\nclass SubUser { void go(Sub s) { Sub.make(); s.make(); } }\n",
    "OnDemand": "package p;\nimport q.gen.*;\nclass OnDemand { void go() { Maker.make(); new Maker(); } }\n",
    "SamePackage": "package p;\nclass SamePackage { void go(Local l) { Local.of(); new Local(); l.go(); } }\n",
    "PackageRoute": "package p;\nclass PackageRoute { void go() { javax.gen.Thing.make(); } }\n",
    "Nested": "package p;\nimport q.gen.Maker.Builder;\nclass Nested { void go() { Builder.of(); new Builder(); } }\n",
    "Implementor": "package p;\nimport q.gen.Handler;\nclass Implementor implements Handler { public void handle() { Handler.none(); } }\n",
}
SOURCES = {f"src/main/java/p/{name}.java": source for name, source in USES.items()}


def graph(make_repo, files: dict):
    return build_graph(make_repo({**MARKERS, **files}), JAVA)


def reasons(g) -> set[str]:
    return set(g.external_reasons)


# ---- the reviewer's cases, one by one ------------------------------------------------------------------------------------------


def only(make_repo, *names: str):
    """The graph of a repo with just these classes of USES and the unread files: each route test is about its own sites, so a route
    that does not ask the question fails ITS test (the matrix at the end makes the sum)."""
    return graph(make_repo, {f"src/main/java/p/{name}.java": USES[name] for name in names} | UNREAD)


def test_a_single_type_import_of_a_type_only_an_unread_file_declares_is_not_a_library_type(make_repo):
    # javac 21: `Maker.make()` is invokestatic q/gen/Maker.make, `new Maker()` is invokespecial q/gen/Maker."<init>" (see test_javac...)
    g = only(make_repo, "Single")
    sites = {(u.caller, u.text): u.reason for u in g.unresolved}
    assert sites[("p.Single.staticCall()", "Maker.make")]
    assert [u for u in g.unresolved if u.caller == "p.Single.creation()" and u.text == "Maker"]
    assert [u for u in g.unresolved if u.caller == "p.Single.param(Maker)" and u.text == "m.run"]
    assert [u for u in g.unresolved if u.caller == "p.Single.caught()" and u.text == "e.report"]
    assert g.external_calls == 0, g.external_reasons


def test_a_full_name_of_a_type_only_an_unread_file_declares_is_not_a_library_type(make_repo):
    g = only(make_repo, "Qualified")
    assert [u for u in g.unresolved if u.caller == "p.Qualified.creation()"]
    assert [u for u in g.unresolved if u.caller == "p.Qualified.staticCall()"]
    assert [u for u in g.unresolved if u.caller == "p.Qualified.param(Maker)" and u.text == "m.run"]
    assert [u for u in g.unresolved if u.caller == "p.Qualified.fieldCall()" and u.text == "field.run"]
    assert not [e for e in g.edges if e.caller.startswith("p.Qualified.")]
    assert g.external_calls == 0, g.external_reasons


def test_a_single_static_import_of_a_member_of_a_type_only_an_unread_file_declares_is_unresolved(make_repo):
    g = only(make_repo, "StaticSingle")
    single = [u for u in g.unresolved if u.caller == "p.StaticSingle.go()"]
    assert single and "q.gen.Maker" in single[0].reason
    assert g.external_calls == 0, g.external_reasons


def test_a_static_on_demand_import_of_a_type_only_an_unread_file_declares_is_unresolved(make_repo):
    g = only(make_repo, "StaticOnDemand")
    ondemand = [u for u in g.unresolved if u.caller == "p.StaticOnDemand.go()"]
    assert ondemand and "q.gen.Maker" in ondemand[0].reason
    assert g.external_calls == 0, g.external_reasons


def test_super_calls_into_a_superclass_only_an_unread_file_declares_are_unresolved(make_repo):
    g = only(make_repo, "Sub")
    assert [u for u in g.unresolved if u.caller == "p.Sub.run()" and u.text == "super.run"]
    assert "super into external base" not in g.external_reasons


def test_super_constructor_calls_into_a_superclass_only_an_unread_file_declares_are_unresolved(make_repo):
    g = only(make_repo, "Sub")
    assert [u for u in g.unresolved if u.caller == "p.Sub.<init>()" and u.text == "super"]
    assert "super(...) into external base" not in g.external_reasons


def test_a_method_a_read_subtype_inherits_from_a_superclass_only_an_unread_file_declares_is_unresolved(make_repo):
    # the static call on a read subtype whose `make` is the unread superclass's, from outside the subtype, through an instance, and unqualified
    g = only(make_repo, "Sub", "SubUser")
    assert [u for u in g.unresolved if u.caller == "p.SubUser.go(Sub)" and u.text == "Sub.make"]
    assert [u for u in g.unresolved if u.caller == "p.SubUser.go(Sub)" and u.text == "s.make"]
    assert [u for u in g.unresolved if u.caller == "p.Sub.unqualified()" and u.text == "make"]
    assert "inherited from an external supertype" not in g.external_reasons


def test_an_on_demand_import_of_a_package_with_an_unread_type_stays_unresolved(make_repo):
    g = only(make_repo, "OnDemand")
    assert {u.caller for u in g.unresolved} >= {"p.OnDemand.go()"} and g.external_calls == 0


def test_a_type_of_the_same_package_that_only_an_unread_file_declares_stays_unresolved(make_repo):
    g = only(make_repo, "SamePackage")
    assert {u.caller for u in g.unresolved} >= {"p.SamePackage.go(Local)"} and g.external_calls == 0


def test_a_full_name_in_a_package_of_the_jdk_that_an_unread_file_declares_is_unresolved(make_repo):
    # javac 21 compiles `javax.gen.Thing.make()` to invokestatic javax/gen/Thing.make when the repo's own javax/gen/Thing.java is there
    g = only(make_repo, "PackageRoute")
    assert [u for u in g.unresolved if u.caller == "p.PackageRoute.go()"]
    assert "static call on external class (JDK)" not in g.external_reasons


def test_a_member_type_of_an_unread_type_is_one_the_file_declares(make_repo):
    g = only(make_repo, "Nested")
    assert [u for u in g.unresolved if u.caller == "p.Nested.go()" and u.text == "Builder.of"]
    assert g.external_calls == 0, g.external_reasons


def test_a_static_call_on_an_interface_only_an_unread_file_declares_is_unresolved(make_repo):
    g = only(make_repo, "Implementor")
    assert [u for u in g.unresolved if u.caller == "p.Implementor.handle()" and u.text == "Handler.none"]
    assert g.external_calls == 0, g.external_reasons


def test_the_anonymous_class_of_a_new_of_an_unread_type_is_still_made(make_repo):
    # `new Maker() { }` makes the anonymous class whatever Maker is: its constructor is an edge, as for a library type
    g = only(make_repo, "Single")
    assert {e.callee for e in g.edges if e.caller == "p.Single.anonymous()"} == {"p.Single.anonymous()$1"}


def test_with_no_unread_file_each_route_is_a_library_type(make_repo):
    # the same sources, Maker in a jar: the routes above ARE external verdicts, and are not refused for nothing
    g = graph(make_repo, SOURCES)
    assert g.external_calls >= 12
    assert "static call on external class (library)" in reasons(g)


def test_a_type_of_an_unrelated_name_is_still_a_library_type_beside_an_unread_file(make_repo):
    files = {
        **UNREAD,
        "src/main/java/p/Other.java": "package p;\nimport q.other.Thing;\nclass Other { void go(Thing t) { Thing.make(); new Thing(); t.stop(); } }\n",
    }
    g = graph(make_repo, files)
    assert g.external_calls == 3 and not [u for u in g.unresolved if u.caller.startswith("p.Other.")]


def test_the_unread_question_asks_the_qualified_name_package_and_type(make_repo):
    # Maker is q.gen.Maker and Thing is javax.gen.Thing, so `q.other.Maker`, `javax.other.Thing` and `javax.gen.Maker` are other types,
    # and the unread files declare none of them
    files = {
        **UNREAD,
        "src/main/java/p/Other.java": (
            "package p;\nimport q.other.Maker;\n"
            "class Other { void go() { Maker.make(); } void full() { javax.other.Thing.make(); } void both() { javax.gen.Maker.make(); } }\n"
        ),
    }
    g = graph(make_repo, files)
    assert g.external_calls == 3 and not [u for u in g.unresolved if u.caller.startswith("p.Other.")]


def test_a_file_whose_package_cannot_be_told_may_declare_any_qualified_type(make_repo, tmp_path):
    # a link out of the repo is not read (java_indexer.read_unread_source): it may declare anything, in any package, so `q.other.Thing`
    # (which no file names) may be what it declares. The same repo without the link has `Thing` as a library type.
    import os

    files = {"src/main/java/p/Other.java": "package p;\nimport q.other.Thing;\nclass Other { void go() { Thing.make(); new Thing(); } }\n"}
    plain = graph(make_repo, files)
    assert plain.external_calls == 2
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "Far.java").write_text("package far;\nclass Far {}\n")
    (tmp_path / "target").mkdir()
    os.symlink(outside / "Far.java", tmp_path / "target" / "Far.java")
    g = build_graph(tmp_path, JAVA)
    assert g.external_calls == 0
    assert [u for u in g.unresolved if u.caller == "p.Other.go()" and "package ripple could not tell" in u.reason]


def test_a_type_of_java_lang_keeps_the_reading_it_always_had(make_repo):
    # an unread file `p/Thread.java` is a repo type of p, which a simple `Thread` means: that is the same-package question, not this one.
    # The full name java.lang.Thread is a type of java.lang, which no repo declares.
    files = {**UNREAD, "src/main/java/p/Use.java": "package p;\nclass Use { void go() { java.lang.Thread.sleep(1); Thread.yield(); } }\n"}
    g = graph(make_repo, files)
    assert not [u for u in g.unresolved if u.caller == "p.Use.go()" and u.text == "java.lang.Thread.sleep"]


# ---- javac: the compiler's word on the routes ----------------------------------------------------------------------------------

JAVAC, JAVAP = shutil.which("javac"), shutil.which("javap")


def test_javac_resolves_every_route_to_the_unread_class(tmp_path):
    """javac 21 (the oracle, as in test_java_javac.py): each route in USES is an invoke instruction on q/gen/Maker (or its sibling), so
    the class declared under target/generated-sources/ is what runs. Skipped where no javac 21 and javap are on PATH."""
    if not JAVAC or not JAVAP:
        pytest.skip("no javac and javap on PATH")
    said = subprocess.run([JAVAC, "-version"], capture_output=True, text=True, check=False)
    if "21" not in said.stdout + said.stderr and "22" not in said.stdout + said.stderr:
        pytest.skip(f"javac -version said {said.stdout + said.stderr!r}")
    sources = []
    for rel, source in {**SOURCES, **UNREAD}.items():
        path = tmp_path / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        sources.append(str(path))
    out = tmp_path / "out"
    out.mkdir()
    done = subprocess.run([JAVAC, "-d", str(out), *sources], capture_output=True, text=True, check=False, timeout=300)
    assert done.returncode == 0, done.stderr
    expected = {
        "Single": ["q/gen/Maker.make", 'q/gen/Maker."<init>"', "q/gen/Maker.run", "q/gen/Boom.report"],
        "Qualified": ["q/gen/Maker.make", 'q/gen/Maker."<init>"', "q/gen/Maker.run"],
        "StaticSingle": ["q/gen/Maker.make"],
        "StaticOnDemand": ["q/gen/Maker.make"],
        "Sub": ['q/gen/Maker."<init>"', "q/gen/Maker.run", "Method make:()Lq/gen/Maker;"],  # (`make()` inside Sub is Sub's own, inherited)
        "SubUser": ["p/Sub.make:()Lq/gen/Maker;"],  # (`Sub.make()` and `s.make()`: the owner is Sub, and Sub's make is Maker's)
        "OnDemand": ["q/gen/Maker.make", 'q/gen/Maker."<init>"'],
        "SamePackage": ["p/Local.of", 'p/Local."<init>"', "p/Local.go"],
        "PackageRoute": ["javax/gen/Thing.make"],
        "Nested": ["q/gen/Maker$Builder.of", 'q/gen/Maker$Builder."<init>"'],
        "Implementor": ["q/gen/Handler.none"],
    }
    for name, calls in expected.items():
        disassembled = subprocess.run([JAVAP, "-c", "-p", "-cp", str(out), f"p.{name}"], capture_output=True, text=True, check=True).stdout
        for call in calls:
            assert call in disassembled, (name, call, disassembled)


# ---- the invariant: every place the resolver takes a type for a library type asks the unread question -----------------------------

# Every external verdict or library type the resolver builds, read off java_resolver.py's own syntax tree: (the function, the second
# element of the tuple) -> what it is. A new one is a new route, and fails test_every_external_tuple_is_classified until it is
# classified here, which means deciding what it does about a type only an unread file declares.
#   "choke"     the one place a library type is made from a qualified name: library(), which asks unread_type.
#   "table"     a type or verdict that is a fact of the language or the JDK table, never a name a repo may declare: Object, the
#               enum and record every such type has above it, a string literal's java.lang.String, an implicit member.
#   "java.lang" a name java.lang supplies: it keeps the reading it always had (unread_type never asks one).
#   "asked"     the function asks unread_type / unresolved_superclass itself before it builds the verdict.
#   "carrier"   the verdict reads a type another place decided (resolve_type, supers_of): it cannot be a library type unless that
#               place said so, and that place is one of the above. Each is exercised by a row of the matrix below.
#   "guarded"   a name that is `?.Name` or java.lang: _on_demand_type asks the unread question of the simple name before it builds it.
#   "test"      a membership test, not a verdict.
ROUTES = {
    ("<module>", "OBJECT"): "table",
    ("supers_of", "resolved[1]"): "carrier",
    ("supers_of", "'java.lang.Enum'"): "table",
    ("supers_of", "'java.lang.Record'"): "table",
    ("library", "qualified"): "choke",
    ("_on_demand_type", "'?.' + name"): "guarded",
    ("_on_demand_type", "'java.lang.' + name"): "java.lang",
    ("_resolve_type", "'unres'"): "test",
    ("_resolve_type", "'tvar'"): "test",
    ("type_of", "'java.lang.String'"): "table",
    ("type_of", "'java.lang.Class'"): "table",
    ("select", "IMPLICIT_RECORD_ACCESSOR"): "table",
    ("select", "OBJECT_MEMBER"): "table",
    ("select", "IMPLICIT_ENUM_MEMBER"): "table",
    ("select", "'inherited from an external supertype'"): "carrier",
    ("_resolve_invocation", "'super into external interface'"): "carrier",
    ("_resolve_invocation", "f'static call on external class {self.label(r[1])}'"): "carrier",
    ("_resolve_invocation", "f'instance of external class {self.label(r[1])}'"): "carrier",
    ("_resolve_invocation", "'array member'"): "table",
    ("_resolve_invocation", "'static call on external class (JDK)'"): "asked",
    ("super_call", "'super into external base'"): "asked",
    ("unqualified", "implicit"): "table",
    ("unqualified", "'inherited from an external supertype'"): "carrier",
    ("unqualified", "f'static import {self.label(owners[0])}'"): "asked",
    ("unqualified", "'static on-demand import (external)'"): "asked",
    ("call", "NO_REPO_METHOD"): "table",
    ("new", "f'new of external class {self.label(r[1])}'"): "carrier",
    ("explicit_constructor", "'super(...) into external base'"): "asked",
    ("explicit_constructor", "'super(...) into java.lang.Object'"): "table",
}
# the questions a function that builds a verdict of kind "asked" calls
THE_QUESTION = {"unread_type", "unresolved_superclass", "library"}


def external_tuples() -> list[tuple[str, str]]:
    tree = ast.parse(Path(java_resolver.__file__).read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # noqa: N802
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_Tuple(self, node):  # noqa: N802
            if len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "ext":
                found.append((stack[-1] if stack else "<module>", ast.unparse(node.elts[1])))
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def calls_of(function: str) -> set[str]:
    tree = ast.parse(Path(java_resolver.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            names |= {n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    return names


def test_every_external_tuple_is_classified():
    found = set(external_tuples())
    assert found - set(ROUTES) == set(), f"a new route to an external verdict or library type: classify it in ROUTES ({found - set(ROUTES)})"
    assert set(ROUTES) - found == set(), f"a route listed that the resolver no longer builds: {set(ROUTES) - found}"


def test_every_route_that_asks_the_unread_question_calls_it():
    for (function, element), kind in ROUTES.items():
        if kind == "asked":
            assert calls_of(function) & THE_QUESTION, (function, element)
    # the choke point is the one place that makes a library type from a name, and it asks
    assert "unread_type" in calls_of("library")
    # and the places that decide a name is a library type reach it through the choke point
    assert "library" in calls_of("find_simple_type") and "library" in calls_of("_resolve_type")


def test_the_places_that_resolve_a_name_build_no_library_type_of_their_own():
    # find_simple_type and _resolve_type decide that a name is not a repo type; neither writes ("ext", name) itself: only library()
    # (and the on-demand reading of a simple name, which asks the unread question of the name and of each package) does
    builders = {f for f, _ in external_tuples() if f in ("find_simple_type", "_resolve_type")}
    assert builders <= {"_resolve_type"}
    assert {e for f, e in external_tuples() if f == "_resolve_type"} <= {"'unres'", "'tvar'"}  # (membership tests)


def run_matrix(make_repo, unread: bool):
    files = {**SOURCES, **(UNREAD if unread else {})}
    return graph(make_repo, files)


# the verdict reasons that rest on a type being a library type: the matrix, run with Maker in a jar, makes every one
TYPE_REASONS = {
    "static call on external class (library)",
    "static call on external class (JDK)",
    "new of external class (library)",
    "inherited from an external supertype",
    "super into external base",
    "super(...) into external base",
    "static import (library)",
    "static on-demand import (external)",
    "instance of external class (library)",
}


def test_the_matrix_reaches_every_route_when_the_type_is_in_a_jar(make_repo):
    g = run_matrix(make_repo, unread=False)
    missing = TYPE_REASONS - reasons(g)
    assert missing == set(), f"a route the matrix does not reach (so the invariant says nothing about it): {missing}"


def test_the_matrix_makes_no_external_verdict_when_only_an_unread_file_declares_the_type(make_repo):
    g = run_matrix(make_repo, unread=True)
    assert g.external_reasons == {}, g.external_reasons  # (every site of the matrix is on Maker, Boom, Handler, Local or Thing)
    assert not [u for u in g.unresolved if not u.reason]

