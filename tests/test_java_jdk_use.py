"""How the Java resolver reads the JDK table (ripple/data/java_jdk.txt) instead of the 60 hand-written JDK 21 types.

The table is the UNION over every released JDK from 8 through 27, so only an absence proves anything and a presence only ever
blocks (and a JDK after 27 is the table's horizon, which ripple cannot see past).
Each test below is one way a naive reading of the table makes a wrong edge or a wrong "external", and every test names the
reading it refuses. They need tree-sitter-java and no JDK. tests/test_java_probes.py holds the same shapes with javac's
answers (bench/fixtures/java/probes/jdk/table-*, bench/fixtures/java/probes/jdk/version-*).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import java_hand_tables as hand
import pytest

pytest.importorskip("tree_sitter_java")

from ripple import java_jdk  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.java_indexer import Declarations  # noqa: E402
from ripple.java_resolver import JavaResolver, _jdk_layout  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def index(make_repo, files: dict):
    return build_graph(make_repo(files), JAVA)


def calls(graph, caller: str) -> set[str]:
    return {e.callee for e in graph.edges if e.caller == caller and e.kind == "call"}


def reasons(graph, caller: str) -> list[str]:
    return [u.reason for u in graph.unresolved if u.caller == caller]


def externals(graph) -> dict[str, int]:
    return dict(graph.external_reasons)


@pytest.fixture(scope="module")
def resolver():
    return JavaResolver(Declarations(), JAVA)


# ---- fields: a known type no longer "declares no field" -------------------------------------------------------------------


def test_a_field_a_jdk_supertype_inherits_hides_the_outer_field_of_that_name(make_repo):
    # Writer.lock is `protected Object lock`: the bare `lock` inside Q is that, not Outer's (JLS 8.2, 6.4.1). Reading a known type
    # as "declares no field" walks on to Outer.lock and proves Item.toString() for a call javac sends to Object.toString().
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Item { public String toString() { return "item"; } }
                class Outer {
                    Item lock = new Item();
                    Item unrelated = new Item();
                    class Q extends java.io.Writer {
                        public void write(char[] b, int o, int l) {}
                        public void flush() {}
                        public void close() {}
                        String f() { return lock.toString() + unrelated.toString(); }
                    }
                }
                """
        },
    )
    assert calls(g, "p.Outer.Q.f()") == {"p.Item.toString()"}  # `unrelated` is no field of any Writer: that one is proven
    assert any("'lock' may be a field inherited from an external base of p.Outer.Q" in r for r in reasons(g, "p.Outer.Q.f()"))


def test_an_inherited_jdk_field_is_found_through_its_subclasses_too(make_repo):
    # FilterInputStream.in (protected), seen from a class that extends FilterInputStream
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Item { int read() { return 1; } }
                class Outer {
                    Item in = new Item();
                    class Q extends java.io.FilterInputStream {
                        Q() { super(null); }
                        int f() throws java.io.IOException { return in.read(); }
                    }
                }
                """
        },
    )
    assert calls(g, "p.Outer.Q.f()") == set()
    assert any("'in' may be a field inherited" in r for r in reasons(g, "p.Outer.Q.f()"))


def test_an_inherited_jdk_field_decides_which_overload_an_argument_picks(make_repo):
    # AbstractList.modCount is an int: javac calls pick(int). Read as Outer's Item field it picks pick(Item).
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Item {}
                class Outer {
                    Item modCount = new Item();
                    static void pick(int n) {}
                    static void pick(Item i) {}
                    class Q extends java.util.AbstractList<String> {
                        public String get(int i) { return ""; }
                        public int size() { return 0; }
                        void f() { pick(modCount); }
                    }
                }
                """
        },
    )
    assert calls(g, "p.Outer.Q.f()") == set()
    assert any("2 overloads of pick" in r for r in reasons(g, "p.Outer.Q.f()"))


def test_a_name_that_no_jdk_gives_the_type_as_a_field_is_the_outer_ones(make_repo, tmp_path):
    # the other direction: Writer has `lock` and no `modCount`, so Outer.modCount is what the bare name means
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Item {}
                class Outer {
                    Item modCount = new Item();
                    static void pick(int n) {}
                    static void pick(Item i) {}
                    class Q extends java.io.Writer {
                        public void write(char[] b, int o, int l) {}
                        public void flush() {}
                        public void close() {}
                        void f() { pick(modCount); }
                    }
                }
                """
        },
    )
    assert calls(g, "p.Outer.Q.f()") == {"p.Outer.pick(Item)"}


# ---- member types ---------------------------------------------------------------------------------------------------------


def test_a_member_type_a_jdk_supertype_inherits_hides_a_repo_type_of_the_same_name(make_repo):
    # HashMap inherits Map.Entry: inside Counts, `Entry` is that, not the p.Entry of the same package or the q.Entry imported
    g = index(
        make_repo,
        {
            "p/Entry.java": "package p;\npublic class Entry { String getKey() { return \"repo\"; } }\n",
            "q/Entry.java": "package q;\npublic class Entry { public String getKey() { return \"other\"; } }\n",
            "p/Counts.java": "package p;\nimport java.util.HashMap;\npublic class Counts extends HashMap<String, Integer> { String f(Entry e) { return e.getKey(); } }\n",
            "p/Imports.java": "package p;\nimport q.Entry;\nimport java.util.HashMap;\npublic class Imports extends HashMap<String, Integer> { String f(Entry e) { return e.getKey(); } }\n",
            "p/Simple.java": "package p;\nimport java.util.HashMap;\npublic class Simple extends HashMap<String, Integer> { class SimpleEntry {} void f() { new SimpleEntry(); } }\n",
        },
    )
    assert calls(g, "p.Counts.f(Entry)") == set() and calls(g, "p.Imports.f(Entry)") == set()
    assert any("type Entry may be a member type that p.Counts inherits from java.util.HashMap" in r for r in reasons(g, "p.Counts.f(Entry)"))
    assert any("type Entry may be a member type that p.Imports inherits from java.util.HashMap" in r for r in reasons(g, "p.Imports.f(Entry)"))


def test_a_package_private_member_type_of_a_jdk_class_is_not_inherited(make_repo):
    # HashMap.Node is package-private: a class in another package does not inherit it, so p.Node stays p.Node. A name no row holds
    # is in no JDK. (And a name that is a member type of Writer's ancestors, none, hides nothing.)
    g = index(
        make_repo,
        {
            "p/Node.java": "package p;\npublic class Node { String getKey() { return \"repo\"; } }\n",
            "p/Counts.java": "package p;\nimport java.util.HashMap;\npublic class Counts extends HashMap<String, Integer> { String f(Node n) { return n.getKey(); } }\n",
            "p/Out.java": "package p;\npublic class Out extends java.io.Writer {\n public void write(char[] b, int o, int l) {}\n public void flush() {}\n public void close() {}\n String f(Entry... e) { return \"\"; }\n}\n",
        },
    )
    assert calls(g, "p.Counts.f(Node)") == {"p.Node.getKey()"}


def test_an_inherited_member_type_hides_the_class_own_name_and_a_class_with_no_such_member_keeps_it(make_repo):
    # JLS 6.4.1: inside `class Entry extends AbstractMap`, Entry is the Map.Entry it inherits, not this class (the probe
    # jdk/inherited-member-type-hides-the-class-name-jdk-supertype holds javac's answer). Resolving it to the class gave the edge
    # p.Entry.first -> p.Entry.getKey, for a call javac sends to Map.Entry.getKey. `Thing` has no member type Thing in any JDK.
    g = index(
        make_repo,
        {
            "p/Entry.java": """\
                package p;
                import java.util.AbstractMap;
                import java.util.Map;
                import java.util.Set;
                public class Entry extends AbstractMap<String, String> {
                    public Set<Map.Entry<String, String>> entrySet() { return null; }
                    public String getKey() { return "mine"; }
                    Object first(Entry e) { return e.getKey(); }
                }
                """,
            "p/Thing.java": """\
                package p;
                public class Thing extends java.util.HashMap<String, String> {
                    Thing self() { return this; }
                    Object copy(Thing t) { return t.self(); }
                }
                """,
        },
    )
    assert calls(g, "p.Entry.first(Entry)") == set()
    assert any("type Entry may be a member type that p.Entry inherits from java.util.AbstractMap" in r for r in reasons(g, "p.Entry.first(Entry)"))
    assert calls(g, "p.Thing.copy(Thing)") == {"p.Thing.self()"}


def test_a_member_type_inherited_from_a_repo_class_hides_the_class_own_name(make_repo):
    # `class Node extends Base`, Base declares `static class Node`: inside Node, Node is Base.Node, and javac calls Base$Node.run.
    # Resolving to the class itself was already a wrong edge before the JDK table (probe shadow/inherited-member-type-hides-...).
    g = index(
        make_repo,
        {
            "p/Node.java": """\
                package p;
                class Base { static class Node { void run() {} } }
                public class Node extends Base {
                    void run() {}
                    void f(Node n) { n.run(); }
                }
                """
        },
    )
    assert calls(g, "p.Node.f(Node)") == {"p.Base.Node.run()"}


# ---- an interface of the JDK that no table type implements is still not "no repo class is one" -----------------------------


def table_interfaces_with_no_table_subtype():
    names = java_jdk.table().names()
    subtypes = {s for name in names for s in java_jdk.jdk_type(name).supers}
    return [n for n in names if java_jdk.jdk_type(n).kind == "interface" and n not in subtypes]


def test_a_jdk_interface_with_no_table_subtype_never_proves_that_no_repo_class_is_one(resolver):
    # `System.Logger` and `ProcessHandle` have no type below them in the table, so the closure test "every table type below it is
    # unextendable" passes vacuously: only the class condition (ctor=no) keeps an interface from being proven. A repo class
    # implements it. (Reading the closure for an interface too made `s.getName()` below a call outside the repo.)
    free = table_interfaces_with_no_table_subtype()
    assert len(free) > 100 and {"java.lang.System.Logger", "java.lang.ProcessHandle"} <= set(free)
    for name in free:
        assert not resolver.no_repo_instance(name), name


def test_a_receiver_typed_with_a_jdk_interface_that_has_no_table_subtype_stays_unresolved_when_a_repo_class_implements_it(make_repo):
    g = index(
        make_repo,
        {
            "p/Use.java": """\
                package p;
                import java.lang.System.Logger;
                abstract class Mine implements Logger { public String getName() { return "repo"; } }
                abstract class Proc implements ProcessHandle { public long pid() { return 1; } }
                public class Use {
                    static String read(Logger s) { return s.getName(); }
                    static long pid(ProcessHandle p) { return p.pid(); }
                }
                """
        },
    )
    for caller, name in (("p.Use.read(Logger)", "getName"), ("p.Use.pid(ProcessHandle)", "pid")):
        assert calls(g, caller) == set()
        assert any(f"a repo class that may be one declares {name}" in r for r in reasons(g, caller)), (caller, reasons(g, caller))
    assert externals(g) == {}


def test_the_same_calls_are_outside_the_repo_when_no_repo_class_implements_the_interface(make_repo):
    g = index(
        make_repo,
        {
            "p/Use.java": """\
                package p;
                public class Use { static String read(System.Logger s) { return s.getName(); } }
                """
        },
    )
    assert sum(externals(g).values()) == 1 and reasons(g, "p.Use.read(Logger)") == []


# ---- a table the loader refuses: every type outside the repo is unknown, and the graph says so ------------------------------


@pytest.fixture
def refused_table(monkeypatch):
    import functools

    from ripple import java_resolver

    real = java_jdk.table()
    text = "# rows: 5\n" + "\n".join(real._lines[n] for n in real.names()[:2]) + "\n"  # the header counts five rows, two are there
    cut = functools.lru_cache(maxsize=1)(lambda: java_jdk.JdkTable(text))
    java_resolver._all_jdks.cache_clear()
    java_resolver._jdk_layout.cache_clear()
    monkeypatch.setattr(java_jdk, "table", cut)
    yield cut
    monkeypatch.undo()
    java_resolver._all_jdks.cache_clear()
    java_resolver._jdk_layout.cache_clear()


WRITER_REPO = {
    "p/A.java": "package p;\nclass A extends java.io.Writer {\n public void write(char[] b, int o, int l) {}\n public void flush() {}\n public void close() {}\n void f() { helper(); }\n void helper() {}\n}\n"
}


def test_a_refused_table_makes_every_type_unknown_says_so_and_a_whole_one_does_not(make_repo, refused_table):
    g = index(make_repo, WRITER_REPO)
    assert java_jdk.jdk_type("java.io.Writer") is None and len(java_jdk.table()) == 0
    assert calls(g, "p.A.f()") == set(), "Writer is unknown, so `helper` may be its"  # with the whole table: {"p.A.helper()"}
    assert len(g.unread) == 1 and "refused" in g.unread[0] and "every type of the JDK is treated as unknown" in g.unread[0]
    assert "5 rows" in g.unread[0] and "2" in g.unread[0]  # the reason says what disagreed


def test_a_refused_table_is_one_note_in_the_index_output(make_repo, refused_table):
    from ripple import cli
    from ripple.languages import LanguageIndex, RepoGraph

    g = index(make_repo, WRITER_REPO)
    out = "\n".join(cli.format_unread(RepoGraph(g, [LanguageIndex("java", 1, 1, unread=list(g.unread))]), named=False))
    assert "not read" in out and "the JDK table" in out and "refused" in out


def test_a_whole_table_leaves_no_note_and_a_run_that_never_looked_a_type_up_has_none_either(make_repo):
    assert index(make_repo, WRITER_REPO).unread == []
    assert index(make_repo, {"p/B.java": "package p;\nclass B { void f() { g(); } void g() {} }\n"}).unread == []


# ---- the override shortcut ------------------------------------------------------------------------------------------------


def test_a_repo_method_with_no_parameters_overrides_a_jdk_method_with_exactly_none(make_repo):
    # Writer.flush() is declared with exactly 0 parameters: Q.flush() overrides it, so the call is Q's
    g = index(
        make_repo,
        {
            "p/A.java": "package p;\nclass Q extends java.io.Writer {\n public void write(char[] b, int o, int l) {}\n public void flush() {}\n public void close() {}\n void f() { flush(); }\n}\n"
        },
    )
    assert calls(g, "p.Q.f()") == {"p.Q.flush()"}


def test_a_varargs_jdk_method_is_not_the_signature_of_a_repo_method_with_no_parameters(make_repo):
    # CompletableFuture.allOf(CompletableFuture<?>...) admits 0 arguments but is not `allOf()`: a repo allOf() overrides nothing of
    # it. (javac does pick Q.allOf() for `allOf()`, phase 1 before varargs: the rule is stricter than it must be, and says so.)
    g = index(
        make_repo,
        {"p/A.java": "package p;\nclass Q extends java.util.concurrent.CompletableFuture<String> {\n static void allOf() {}\n void f() { allOf(); }\n}\n"},
    )
    assert calls(g, "p.Q.f()") == set()
    assert any("may declare another allOf taking 0 arguments" in r for r in reasons(g, "p.Q.f()"))


def test_a_varargs_jdk_method_takes_the_arguments_of_a_repo_method_beside_it(make_repo):
    # PrintStream.printf(String, Object...) takes one argument too: javac sends log.printf("x") there, never to Log.printf(Item)
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Item {}
                class Log extends java.io.PrintStream {
                    Log() { super(System.out); }
                    void printf(Item i) {}
                    void f(Item i) { printf("x"); printf(i); printf("%s", i); }
                }
                """
        },
    )
    assert calls(g, "p.Log.f(Item)") == set()  # printf("%s", i) has two arguments: no repo method takes them, it is the JDK's
    assert all("may declare another printf" in r for r in reasons(g, "p.Log.f(Item)")) and len(reasons(g, "p.Log.f(Item)")) == 2


# ---- members that only some JDKs have -------------------------------------------------------------------------------------


def test_a_method_only_jdk_8_has_may_be_the_threads_not_the_outer_classes(make_repo):
    # Thread.destroy() is JDK 8 only (javac 8 sends destroy() in Q to Thread.destroy, javac 21 to Outer.destroy)
    g = index(
        make_repo,
        {"p/A.java": "package p;\nclass Outer {\n void destroy() {}\n void helper() {}\n class Q extends Thread { void f() { destroy(); helper(); } }\n}\n"},
    )
    assert calls(g, "p.Outer.Q.f()") == {"p.Outer.helper()"}  # `helper` is in no JDK's Thread: the outer class's, proven
    assert any("'destroy' may be inherited from external base java.lang.Thread" in r for r in reasons(g, "p.Outer.Q.f()"))


def test_a_method_only_jdk_21_has_may_be_the_lists_not_the_outer_classes(make_repo):
    # ArrayList.getFirst() is JDK 21 and later (javac 8 and 17 send it to Outer.getFirst, javac 21 to ArrayList.getFirst)
    g = index(
        make_repo,
        {"p/A.java": "package p;\nclass Outer {\n Object getFirst() { return null; }\n class Q extends java.util.ArrayList<String> { Object f() { return getFirst(); } }\n}\n"},
    )
    assert calls(g, "p.Outer.Q.f()") == set()
    assert any("'getFirst' may be inherited from external base java.util.ArrayList" in r for r in reasons(g, "p.Outer.Q.f()"))


def test_a_version_only_name_nobody_in_the_repo_declares_is_still_outside_the_repo(make_repo):
    # no outer class or import could be meant, and the call compiles on a JDK that has the method: it is the JDK's
    g = index(make_repo, {"p/A.java": "package p;\nclass Q extends java.util.ArrayList<String> { Object f() { return getFirst(); } }\n"})
    assert calls(g, "p.Q.f()") == set() and externals(g).get("inherited from an external supertype") == 1


def test_an_enum_method_only_later_jdks_have_is_not_an_implicit_member(make_repo):
    # Enum.describeConstable() is JDK 12 and later: on JDK 8 an unqualified describeConstable() in Op is Outer's
    g = index(
        make_repo,
        {"p/A.java": "package p;\nclass Outer {\n Object describeConstable() { return null; }\n enum Op { ADD; Object f() { return describeConstable(); } }\n}\n"},
    )
    assert calls(g, "p.Outer.Op.f()") == set()
    assert "implicit enum member" not in externals(g)


def test_an_enums_own_values_and_valueof_are_still_the_compilers(make_repo):
    g = index(
        make_repo,
        {"p/A.java": "package p;\nenum Op { ADD; static Op first() { return values()[0]; } static Op of(String s) { return valueOf(s); } }\n"},
    )
    assert externals(g).get("implicit enum member") == 2


# ---- no repo type is an instance of T -------------------------------------------------------------------------------------

OUTSIDE = """\
    package p;
    class Holder extends java.awt.Point {
        public String trim() { return ""; }
        void sort(int[] a) {}
        public Object get() { return null; }
        public void run() {}
    }
    class Weak extends java.lang.ref.WeakReference<Object> {
        Weak(Object o) { super(o); }
        public Object get() { return null; }
    }
    class Spare extends java.util.ArrayList<String> { public void run() {} }
    class Use {
        String a(String s) { return s.trim(); }
        void b(java.util.Arrays a, int[] x) { a.sort(x); }
        Object c(java.lang.ref.Reference<Object> r) { return r.get(); }
        void d(Runnable r) { r.run(); }
    }
    """


def test_a_call_on_a_value_no_repo_type_can_be_is_outside_the_repo_and_the_rest_are_not(make_repo):
    # Holder extends a type the table does not hold, so it may be anything: only a proof from the table keeps the call outside.
    g = index(make_repo, {"p/A.java": OUTSIDE})
    use = "p.Use.a(String)"
    assert reasons(g, use) == []  # String is final in every JDK
    assert reasons(g, "p.Use.b(Arrays,int[])") == []  # Arrays is not final before JDK 21, but its only constructor is private
    assert any("a repo class that may be one declares get" in r for r in reasons(g, "p.Use.c(Reference)"))  # WeakReference has a public one
    # an interface proves nothing, but ArrayList is not a Runnable and Holder is unknown: only Holder may be one
    assert any("a repo class that may be one declares run" in r for r in reasons(g, "p.Use.d(Runnable)"))


def test_a_jdk_class_with_a_non_public_constructor_proves_nothing_when_a_table_type_below_it_has_a_public_one(resolver):
    # Reference and CallSite have package-private constructors, but WeakReference and ConstantCallSite have public ones
    for name in ("java.lang.ref.Reference", "java.lang.invoke.CallSite", "java.util.IllegalFormatException"):
        row = java_jdk.jdk_type(name)
        assert row.kind == "class" and row.ctor is False and not row.final
        assert not resolver.no_repo_instance(name), name
    for name in ("java.util.Arrays", "java.util.Collections", "java.util.concurrent.Executors", "java.nio.ByteBuffer", "java.lang.Runtime"):
        assert java_jdk.jdk_type(name).ctor is False and not java_jdk.jdk_type(name).final
        assert resolver.no_repo_instance(name), name


def test_an_interface_an_enum_and_a_class_with_a_constructor_prove_nothing(resolver):
    for name in ("java.lang.Runnable", "java.util.List", "java.util.Map.Entry", "java.util.ArrayList", "java.lang.Object", "java.lang.Thread", "java.io.Writer"):
        assert not resolver.no_repo_instance(name), name
    for name in ("com.example.Nope", "?.Foo", "java.awt.Point", "java.util.Map$Entry"):  # not in the table: unknown
        assert not resolver.no_repo_instance(name), name


def test_every_hand_written_final_type_is_still_proven_final_and_nothing_else_moved(resolver):
    assert all(resolver.no_repo_instance(name) for name in hand.FINAL_EXTERNAL)
    assert {n for n in hand.FINAL_EXTERNAL if not java_jdk.jdk_type(n).final} == {"java.util.Arrays"}  # proven by the closure, not `final`


# ---- which repo types may be an instance of a JDK type --------------------------------------------------------------------


def test_a_receiver_typed_with_a_jdk_interface_is_outside_the_repo_only_when_no_repo_class_may_be_one(make_repo):
    # Fifo is a LinkedList, so a Queue; Tags is an ArrayList, which is no Queue and no Set: with the table both are known
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                import java.util.ArrayList;
                import java.util.LinkedList;
                import java.util.Queue;
                import java.util.Set;
                class Fifo extends LinkedList<String> { public boolean add(String s) { return true; } }
                class Tags extends ArrayList<String> { public boolean add(String s) { return true; } }
                class Use {
                    boolean q(Queue<String> q) { return q.add("x"); }
                    boolean s(Set<String> s) { return s.add("x"); }
                }
                """
        },
    )
    assert any("a repo class that may be one declares add" in r for r in reasons(g, "p.Use.q(Queue)"))
    assert reasons(g, "p.Use.s(Set)") == []  # neither Fifo nor Tags is a Set, in any of the twenty JDKs


def test_a_name_that_is_not_canonical_is_compared_by_its_simple_name_and_only_ever_says_yes(make_repo):
    # Pair is a Map.Entry. `HashMap.Entry` (a member type spelled through a subtype) and the on-demand `Entry` (no package) both
    # spell it without its canonical name. With an on-demand import of a type outside the repo every qualified name is read as
    # `?.java.util...` too, so the file with `a` has none.
    g = index(
        make_repo,
        {
            "p/Pair.java": """\
                package p;
                class Pair implements java.util.Map.Entry<String, String> {
                    public String getKey() { return ""; }
                    public String getValue() { return ""; }
                    public String setValue(String s) { return s; }
                }
                class Use {
                    String a(java.util.HashMap.Entry<String, String> e) { return e.getKey(); }
                    String c(java.util.Map.Entry<String, String> e) { return e.getKey(); }
                    String d(java.util.Set<String> s) { return s.toString(); }
                }
                """,
            "p/Use2.java": """\
                package p;
                import java.util.Map.*;
                class Use2 { String b(Entry<String, String> e) { return e.getKey(); } }
                """,
        },
    )
    for site in ("p.Use.a(Entry)", "p.Use2.b(Entry)", "p.Use.c(Entry)"):
        assert any("a repo class that may be one declares getKey" in r for r in reasons(g, site)), site


def test_a_library_type_that_no_jdk_type_extends_is_outside_the_repo(make_repo):
    # Pair is a Map.Entry; a value of the library's Entry-less type Widget cannot be one (the table says what is above Map.Entry)
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                import lib.Widget;
                class Pair implements java.util.Map.Entry<String, String> {
                    public String getKey() { return ""; }
                    public String getValue() { return ""; }
                    public String setValue(String s) { return s; }
                }
                class Use { String a(Widget w) { return w.getKey(); } }
                """
        },
    )
    assert reasons(g, "p.Use.a(Widget)") == []


def test_an_implicit_java_lang_name_that_some_jdks_lack_may_be_a_librarys_through_an_on_demand_import(make_repo):
    # java.lang.Module is final (JDK 9 and later): a call on a Module is outside the repo. But on JDK 8 the name in a file with
    # `import com.lib.*;` is the library's Module, which Impl implements (the profile's java.lang list names Module for every JDK).
    g = index(
        make_repo,
        {
            "p/Impl.java": "package p;\nclass Impl implements com.lib.Module { public void configure() {} }\n",
            "p/Use.java": "package p;\nimport com.lib.*;\nclass Use { void f(Module m, String s) { m.configure(); s.trim(); } }\n",
        },
    )
    assert any("a repo class that may be one declares configure" in r for r in reasons(g, "p.Use.f(Module,String)"))
    assert not any("trim" in r for r in reasons(g, "p.Use.f(Module,String)"))  # String is in every JDK: only java.lang can supply it


# ---- a repo that declares a type in a package of the JDK itself -----------------------------------------------------------

PATCHED = """\
    package %s;
    class Marker { public String toString() { return "m"; } }
    class Outer {
        Marker elementData = new Marker();
        class Q extends java.util.ArrayList<String> {
            String f() { return elementData.toString(); }
        }
    }
    """


def test_a_repo_type_in_a_package_of_the_jdk_reaches_what_the_table_leaves_out(make_repo):
    # in java.util, ArrayList.elementData (package-private) is inherited by Q: the table lists no package-private member, so the
    # table is refused for this repo. In package p it is not inherited and Outer.elementData is what the name means.
    patched = index(make_repo, {"java/util/A.java": PATCHED % "java.util"})
    assert calls(patched, "java.util.Outer.Q.f()") == set()
    assert any("'elementData' may be a field inherited from an external base" in r for r in reasons(patched, "java.util.Outer.Q.f()"))


def test_a_repo_in_another_package_keeps_the_table(make_repo):
    g = index(make_repo, {"p/A.java": PATCHED % "p"})
    assert calls(g, "p.Outer.Q.f()") == {"p.Marker.toString()"}


def test_a_patched_repo_refuses_the_table_for_every_supertype_not_only_the_ones_in_its_package(make_repo):
    g = index(
        make_repo,
        {
            "java/util/A.java": "package java.util;\nclass Mine {}\n",
            "p/B.java": "package p;\nclass Outer {\n void destroy() {}\n class Q extends java.lang.Thread { void f() { helper(); } }\n void helper() {}\n}\n",
        },
    )
    assert calls(g, "p.Outer.Q.f()") == set()  # Thread is unknown here, so `helper` may be Thread's
    resolver = JavaResolver(Declarations(), JAVA)
    assert resolver.jdk_row("java.lang.Thread") is not None  # an unpatched repo reads it


def test_the_resolver_the_loader_and_the_readme_state_the_horizon_the_file_has():
    # the table covers JDK 8 through 27: the three places that say so must agree with the header (tests/test_java_jdk.py pins it)
    from ripple import java_resolver

    assert java_resolver._all_jdks() == tuple(range(8, 28))
    for path, wanted in (("README.md", "JDK 8 through 27"), ("ripple/java_jdk.py", "from 8 through 27"), ("ripple/java_resolver.py", "from 8 through 27")):
        assert wanted in (ROOT / path).read_text(encoding="utf-8"), path
    assert "assumption" in (ROOT / "README.md").read_text(encoding="utf-8").lower()


def test_the_packages_of_the_table_are_found_from_its_names():
    packages, nested = _jdk_layout()
    assert {"java.util", "java.io", "java.lang", "javax.tools", "java.util.concurrent.atomic"} <= packages
    assert "java.util.Map" not in packages and "" not in packages
    assert {"Entry", "SimpleEntry", "State"} <= nested and "Map" not in nested
    for name in java_jdk.table().names():  # every name has a capital segment: the convention the package split relies on
        assert any(part[:1].isupper() for part in name.split(".")), name


# ---- the hand tables as a baseline ----------------------------------------------------------------------------------------


def test_everything_the_hand_tables_say_a_type_may_declare_is_still_blocked(resolver):
    for name, members in hand.KNOWN_MEMBERS.items():
        for member, counts in members.items():
            for n in (None,) if hand.ANY[0] in counts else counts:
                assert resolver.may_declare(("ext", name), member, n), (name, member, n)


def test_a_type_is_unknown_when_the_hand_tables_knew_it_and_the_table_does_not(resolver):
    # java.beans is java.desktop's: unknown now, so it may declare anything; the hand table is not kept for it
    assert java_jdk.jdk_type("java.beans.VetoableChangeListener") is None
    assert resolver.may_declare(("ext", "java.beans.VetoableChangeListener"), "anything", 3)
    assert resolver.may_declare(("ext", "java.beans.VetoableChangeListener"), "x", field=True)
    assert resolver._ext_row(("ext", "java.beans.VetoableChangeListener")) is None


def test_the_language_puts_a_record_and_an_enums_own_methods_under_the_type_and_the_table_does_the_rest(resolver):
    assert not resolver.may_declare(("ext", "java.lang.Record"), "anything", 0)
    assert resolver.may_declare(("ext", "java.lang.Enum"), "values", 0) and resolver.may_declare(("ext", "java.lang.Enum"), "valueOf", 1)
    assert resolver.may_declare(("ext", "java.lang.Enum"), "ordinal", 0) and not resolver.may_declare(("ext", "java.lang.Enum"), "ordinal", 1)
    assert not resolver.may_declare(("ext", "java.lang.Enum"), "nothing", 0)
    assert not resolver.may_declare(("ext", "java.lang.Enum"), "values", 0, field=True)
    assert resolver.may_declare(("unres", "Whatever"), "x", 0)


def test_a_jdk_field_is_read_from_the_table_and_a_bare_name_it_lacks_is_not_blocked(resolver):
    assert resolver.may_declare(("ext", "java.io.Writer"), "lock", field=True)
    assert resolver.may_declare(("ext", "java.util.AbstractList"), "modCount", field=True)
    assert resolver.may_declare(("ext", "java.io.FilterInputStream"), "in", field=True)
    assert not resolver.may_declare(("ext", "java.io.Writer"), "modCount", field=True)
    assert not resolver.may_declare(("ext", "java.io.Writer"), "lock", 0)  # a field is no method


def test_declares_no_parameters_asks_the_exact_counts_and_never_admits(resolver):
    allof = java_jdk.jdk_type("java.util.concurrent.CompletableFuture").methods["allOf"]
    assert allof.admits(0) and 0 not in allof.exact
    assert not resolver.declares_no_parameters(("ext", "java.util.concurrent.CompletableFuture"), "allOf")
    assert resolver.declares_no_parameters(("ext", "java.io.Writer"), "flush")
    assert not resolver.declares_no_parameters(("ext", "java.io.Writer"), "write")
    assert not resolver.declares_no_parameters(("ext", "java.awt.Point"), "flush")  # unknown type: nothing is known


# ---- the data file is still read lazily -----------------------------------------------------------------------------------


def run_audited(code: str):
    prelude = (
        "import sys, tempfile, textwrap, pathlib\n"
        "opened = []\n"
        "sys.addaudithook(lambda event, args: opened.append(str(args[0])) if event == 'open' and 'java_jdk.txt' in str(args[0]) else None)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(code)], cwd=ROOT, env={"PYTHONPATH": str(ROOT), "PATH": ""}, capture_output=True, text=True
    )


def test_a_run_that_never_indexes_java_never_opens_the_table_and_a_java_run_with_a_jdk_supertype_does():
    code = """
        from ripple.indexer import build_graph
        from ripple.profile import JAVA, PYTHON
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "m.py").write_text("def f():\\n    return 1\\n")
            build_graph(root, PYTHON)
            assert not opened, opened
            (root / "p").mkdir()
            (root / "p" / "A.java").write_text("package p;\\nclass A { void f() { g(); } void g() {} }\\n")
            build_graph(root, JAVA)
            assert not opened, "a Java repo with no supertype outside the repo has nothing to look up: " + str(opened)
            (root / "p" / "B.java").write_text("package p;\\nclass B extends java.io.Writer { void f() { flush(); } }\\n")
            build_graph(root, JAVA)
            assert opened, "the audit hook never saw the read, so it proves nothing"
        """
    done = run_audited(code)
    assert done.returncode == 0, done.stderr[-2000:]


# ---- a graph cached before the resolver read the table is not served ---------------------------------------------------------


def test_a_java_graph_cached_by_the_code_that_used_the_hand_tables_is_rebuilt(make_repo, tmp_path_factory):
    import json

    from ripple import __version__
    from ripple.cache import CACHE_FORMAT, cache_file, load_graph

    repo = make_repo({"p/A.java": "package p;\nclass A extends java.io.Writer {\n public void write(char[] b, int o, int l) {}\n public void flush() {}\n public void close() {}\n void f() { helper(); }\n void helper() {}\n}\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    graph, cached = load_graph(repo, cache_dir, profile=JAVA)
    assert not cached and calls(graph, "p.A.f()") == {"p.A.helper()"}
    path = cache_file(repo.resolve(), cache_dir, JAVA)
    saved = json.loads(path.read_text())
    assert saved["format"] == CACHE_FORMAT == f"{__version__}-29"  # the table shipped under -17, and -16 is the format before it
    assert load_graph(repo, cache_dir, profile=JAVA)[1] is True
    saved["format"] = f"{__version__}-16"  # (the table was regenerated from twenty JDKs without another bump: this is still the one)
    saved["graph"]["edges"] = []  # what the old code's answer for this repo would have been: nothing proven past Writer
    path.write_text(json.dumps(saved))
    rebuilt, cached = load_graph(repo, cache_dir, profile=JAVA)
    assert cached is False and calls(rebuilt, "p.A.f()") == {"p.A.helper()"}
