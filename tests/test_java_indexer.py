"""The Java front end and resolver on small repos: ids, source roots, and each rule of java_resolver.py both ways.

Every repo here is a few lines of Java written to one temp dir. These need tree-sitter-java and no JDK.
"""

import pytest

pytest.importorskip("tree_sitter_java")

from ripple.indexer import build_graph  # noqa: E402
from ripple.java_indexer import reads_as_source  # noqa: E402
from ripple.profile import JAVA  # noqa: E402


def index(make_repo, files: dict):
    return build_graph(make_repo(files), JAVA)


def edges(graph, caller: str) -> set[str]:
    return {f"{e.kind} {e.callee}" for e in graph.edges if e.caller == caller}


def reasons(graph, caller: str) -> list[str]:
    return [u.reason for u in graph.unresolved if u.caller == caller]


def calls(graph, caller: str) -> set[str]:
    return {e.callee for e in graph.edges if e.caller == caller and e.kind == "call"}


# ---- ids -----------------------------------------------------------------------------------------------------------


def test_ids_are_dotted_with_the_parameter_types_as_written(make_repo):
    g = index(
        make_repo,
        {
            "src/main/java/p/A.java": """\
                package p;
                import java.util.*;
                public class A {
                    public A() {}
                    public A(int n, String... rest) {}
                    void m(Map.Entry<String, List<A>> e, int[][] grid, T t) {}
                    <T> void gen(T x) {}
                    class Inner { void go() {} }
                    static class Nested { Nested(A outer) {} }
                }
                interface I { default void d() {} }
                """
        },
    )
    assert set(g.symbols) >= {
        "src/main/java/p/A.java",
        "p.A", "p.A.<init>()", "p.A.<init>(int,String...)",
        "p.A.m(Entry,int[][],T)", "p.A.gen(T)", "p.A.Inner", "p.A.Inner.go()", "p.A.Nested.<init>(A)", "p.I.d()",
    }  # fmt: skip
    assert g.symbols["p.A"].kind == "class" and g.symbols["p.A.gen(T)"].kind == "method"
    assert g.symbols["src/main/java/p/A.java"].kind == "module"
    assert g.modules == {"src/main/java/p/A.java": "src/main/java/p/A.java"}  # a file's id is its path, never a class's id


def test_anonymous_local_and_enum_constant_classes_are_named_under_their_member(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                public class A {
                    Runnable first = new Runnable() { public void run() {} };
                    void m() {
                        Runnable a = new Runnable() { public void run() {} };
                        Runnable b = new Runnable() { public void run() {} };
                        class Local { void x() {} }
                    }
                    enum Op { ADD { int f() { return 1; } }; abstract int f(); }
                    record R(int a) { R { } int twice() { return a * 2; } }
                }
                """
        },
    )
    assert set(g.symbols) >= {
        "p.A$1", "p.A$1.run()", "p.A.m()$1", "p.A.m()$2", "p.A.m()$2.run()", "p.A.m().Local", "p.A.m().Local.x()",
        "p.A.Op.ADD", "p.A.Op.ADD.f()", "p.A.R.<init>(int)", "p.A.R.twice()",
    }  # fmt: skip
    assert g.bases["p.A.Op.ADD"] == ["p.A.Op"]


def test_two_overloads_written_alike_get_a_clash_suffix(make_repo):
    g = index(make_repo, {"p/A.java": "package p;\nclass A {\n <T extends A> void f(T t) {}\n <T extends Number> void f(T t) {}\n}\n"})
    assert {"p.A.f(T)", "p.A.f(T)#2"} <= set(g.symbols)


# ---- source roots, skipped files, clashes -----------------------------------------------------------------------------


def test_a_file_whose_directory_is_not_its_package_is_not_indexed_and_says_why(make_repo):
    g = index(
        make_repo,
        {
            "src/main/java/p/A.java": "package p;\npublic class A { void f() {} }\n",
            "src/test/resources/data/B.java": "package q;\npublic class B { void g() {} }\n",
            "tools/C.java": "public class C { void h() {} }\n",  # the default package: indexed with no package
        },
    )
    assert "q.B" not in g.symbols and "src/test/resources/data/B.java" not in g.modules
    assert [e for e in g.parse_errors if e.startswith("src/test/resources/data/B.java") and "does not match its directory" in e]
    assert {"p.A.f()", "C.h()"} <= set(g.symbols)


def test_two_files_that_define_one_name_are_neither_indexed_and_calls_of_it_are_unresolved(make_repo):
    g = index(
        make_repo,
        {
            "free/java/p/Flavor.java": "package p;\npublic class Flavor { public String name() { return \"free\"; } }\n",
            "play/java/p/Flavor.java": "package p;\npublic class Flavor { public String name() { return \"play\"; } }\n",
            "src/main/java/p/Use.java": "package p;\npublic class Use { String f(Flavor x) { return x.name(); } }\n",
        },
    )
    assert "p.Flavor" not in g.symbols and "p.Flavor.name()" not in g.symbols
    assert g.module_collisions() == {"p.Flavor": ["free/java/p/Flavor.java", "play/java/p/Flavor.java"]}
    assert calls(g, "p.Use.f(Flavor)") == set()
    assert any("two files define p.Flavor" in r for r in reasons(g, "p.Use.f(Flavor)"))


def test_build_output_is_not_source_when_the_build_file_that_writes_it_is_there(make_repo):
    repo = make_repo(
        {
            "pom.xml": "<project/>",
            "src/main/java/p/A.java": "package p;\npublic class A { void f() {} }\n",
            "target/generated-sources/p/A.java": "package p;\npublic class A { void stale() {} }\n",
            "mod/build.gradle": "",
            "mod/build/generated/q/G.java": "package q;\npublic class G {}\n",
            "mod/src/q/H.java": "package q;\npublic class H { void h() {} }\n",
            "other/target/p2/K.java": "package p2;\npublic class K { void k() {} }\n",  # no pom.xml beside this target
        }
    )
    g = build_graph(repo, JAVA)
    assert "p.A.f()" in g.symbols and "p.A.stale()" not in g.symbols and "q.G" not in g.symbols
    assert "q.H.h()" in g.symbols
    assert "p2.K.k()" in g.symbols  # a directory called target is source when no pom.xml says it is Maven's output
    assert "target/generated-sources/p/A.java" not in g.modules and "p.A" in g.symbols


def test_reads_as_source_looks_up_to_the_repo_root_only(tmp_path):
    inner = tmp_path / "target" / "proj"
    (inner / "src").mkdir(parents=True)
    (inner / ".git").mkdir()
    (tmp_path / "pom.xml").write_text("<project/>")
    assert reads_as_source(inner / "src" / "A.java", JAVA)  # the repo is inside a directory called target: not skipped
    (tmp_path / "x" / "target").mkdir(parents=True)
    (tmp_path / "x" / "pom.xml").write_text("<project/>")
    assert not reads_as_source(tmp_path / "x" / "target" / "A.java", JAVA)


def test_package_info_and_module_info_are_not_read(make_repo):
    repo = make_repo(
        {
            "p/package-info.java": "/** docs */\npackage p;\n",
            "module-info.java": "module m { requires java.base; }\n",
            "p/A.java": "package p;\nclass A {}\n",
        }
    )
    g = build_graph(repo, JAVA)
    assert set(g.modules) == {"p/A.java"}


def test_a_file_with_a_syntax_error_is_listed_and_still_read_as_far_as_it_parses(make_repo):
    g = index(make_repo, {"p/A.java": "package p;\npublic class A {\n void ok() {}\n void bad( {\n void later() { ok(); }\n}\n"})
    assert g.parse_errors == ["p/A.java"]
    assert "p.A.ok()" in g.symbols and g.modules == {"p/A.java": "p/A.java"}


# ---- the comb rule, names, receivers ------------------------------------------------------------------------------------


def test_an_unqualified_call_is_the_innermost_class_that_has_the_name(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                public class A {
                    void helper() {}
                    void own() {}
                    class Inner {
                        void own() {}
                        void go() { helper(); own(); }
                    }
                }
                """
        },
    )
    assert calls(g, "p.A.Inner.go()") == {"p.A.helper()", "p.A.Inner.own()"}


def test_a_member_every_class_has_ends_the_search_in_the_inner_class(make_repo):
    # Inner has toString() from Object, so toString() here is Inner's and never the outer class's override
    g = index(
        make_repo,
        {"p/A.java": "package p;\npublic class A {\n public String toString() { return \"A\"; }\n class Inner { String go() { return toString(); } }\n}\n"},
    )
    assert calls(g, "p.A.Inner.go()") == set()
    assert g.external_reasons.get("java.lang.Object member") == 1


def test_a_static_method_of_an_interface_is_not_a_member_of_its_implementor(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                interface I { static void helper() {} }
                public class A {
                    static void helper() {}
                    static class B implements I { void go() { helper(); } }
                }
                """
        },
    )
    assert calls(g, "p.A.B.go()") == {"p.A.helper()"}


def test_a_variable_hides_a_type_of_the_same_name(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Format { static String print() { return ""; } }
                class Printer { String print() { return ""; } }
                class A {
                    String viaVariable() { Printer Format = new Printer(); return Format.print(); }
                    String viaType() { return Format.print(); }
                }
                """
        },
    )
    assert calls(g, "p.A.viaVariable()") == {"p.Printer.print()"}
    assert calls(g, "p.A.viaType()") == {"p.Format.print()"}


def test_names_are_resolved_through_imports_and_the_package(make_repo):
    g = index(
        make_repo,
        {
            "a/Util.java": "package a;\npublic class Util { public static void one() {} public static void two() {} }\n",
            "b/Twin.java": "package b;\npublic class Twin { public void go() {} }\n",
            "c/Twin.java": "package c;\npublic class Twin { public void go() {} }\n",
            "d/Use.java": """\
                package d;
                import a.Util;
                import b.*;
                import c.*;
                import static a.Util.two;
                public class Use {
                    void f(Twin t) { Util.one(); two(); t.go(); }
                    void g(b.Twin t) { t.go(); }
                }
                """,
        },
    )
    assert calls(g, "d.Use.f(Twin)") == {"a.Util.one()", "a.Util.two()"}
    assert any("two on-demand imports provide Twin" in r or "type Twin" in r for r in reasons(g, "d.Use.f(Twin)"))
    assert calls(g, "d.Use.g(Twin)") == {"b.Twin.go()"}


# ---- overloads: the exact-match rule -----------------------------------------------------------------------------------


OVERLOADS = """\
    package p;
    class Item {}
    class Cart {
        void add(Item item) {}
        void add(String name) {}
        void add(String name, int n) {}
        void addAll(Item a, Item b) {}
        void addAll(Item a, String b) {}
    }
    class Use {
        Cart cart = new Cart();
        void byArity() { cart.add("a", 1); }
        void byExactTypes(Item i, String s) { cart.add(i); cart.add(s); cart.addAll(i, s); }
        void byLiterals() { cart.add("lit"); cart.add(new Item()); }
        void unknown(java.util.List<Item> xs, Object o) { cart.addAll(xs.get(0), xs.get(1)); cart.add(o); }
    }
    """


def test_overloads_are_picked_only_by_arity_or_by_exact_written_types(make_repo):
    g = index(make_repo, {"p/A.java": OVERLOADS})
    assert calls(g, "p.Use.byArity()") == {"p.Cart.add(String,int)"}
    assert calls(g, "p.Use.byExactTypes(Item,String)") == {"p.Cart.add(Item)", "p.Cart.add(String)", "p.Cart.addAll(Item,String)"}
    assert calls(g, "p.Use.byLiterals()") == {"p.Cart.add(String)", "p.Cart.add(Item)"}


def test_overloads_whose_argument_types_are_not_all_known_are_unresolved_never_picked(make_repo):
    g = index(make_repo, {"p/A.java": OVERLOADS})
    assert calls(g, "p.Use.unknown(List,Object)") == set()
    assert sorted(reasons(g, "p.Use.unknown(List,Object)")) == [
        "2 overloads of add on p.Cart take 1 arguments",
        "2 overloads of addAll on p.Cart take 2 arguments",
    ]


def test_an_overload_in_a_subclass_does_not_hide_the_superclass_ones(make_repo):
    # the shared Resolver's measured wrong edge: the first class with the NAME won
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Item {}
                class Cart { void add(String name) {} void add(Item item) {} }
                class Bulk extends Cart {
                    void add(Item item, int times) {}
                    void fill() { add("bulk"); add(new Item(), 2); }
                }
                """
        },
    )
    assert calls(g, "p.Bulk.fill()") == {"p.Cart.add(String)", "p.Bulk.add(Item,int)"}


def test_an_overload_that_overrides_a_closer_one_is_not_a_second_candidate(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Base { void run(String s) {} }
                class Sub extends Base { void run(String s) {} void go() { run("x"); } }
                class Use { void f(Sub s) { s.run("y"); } void g(Base b) { b.run("z"); } }
                """
        },
    )
    assert calls(g, "p.Sub.go()") == {"p.Sub.run(String)"} and calls(g, "p.Use.f(Sub)") == {"p.Sub.run(String)"}
    assert edges(g, "p.Use.g(Base)") == {"call p.Base.run(String)", "override p.Sub.run(String)"}


def test_a_repo_overload_of_an_object_method_is_not_the_one_a_call_of_the_original_means(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Item { boolean equals(Item other) { return true; } }
                class Use {
                    void f(Item a, Item b, Object o, String s) { a.equals(b); a.equals(o); a.equals(s); }
                }
                """
        },
    )
    assert calls(g, "p.Use.f(Item,Item,Object,String)") == {"p.Item.equals(Item)"}  # only the exact type is proven to be it
    assert len(reasons(g, "p.Use.f(Item,Item,Object,String)")) == 2  # an Object or a String may be Object.equals's


def test_an_override_of_an_object_method_is_found_by_arity(make_repo):
    g = index(make_repo, {"p/A.java": "package p;\nclass Item { public boolean equals(Object o) { return true; } }\nclass Use { boolean f(Item a, Object b) { return a.equals(b); } }\n"})
    assert calls(g, "p.Use.f(Item,Object)") == {"p.Item.equals(Object)"}


# ---- the strict rule -----------------------------------------------------------------------------------------------------


def test_a_method_of_a_class_with_a_library_supertype_is_not_proven(make_repo):
    # Writer declares write(String): the repo's write(Item) cannot be said to be the one a call of write means
    g = index(
        make_repo,
        {
            "p/Item.java": "package p;\npublic class Item {}\n",
            "p/Ledger.java": """\
                package p;
                import java.io.Writer;
                public class Ledger extends Writer {
                    public void write(Item item) {}
                    public void write(char[] b, int o, int l) {}
                    public void flush() {}
                    public void close() {}
                    void record(Item i) throws Exception { write(i); write("sep"); this.write("x"); }
                    void helper() {}
                }
                class Use { void f(Ledger l, Item i) throws Exception { l.write(i); l.helper(); } }
                """,
        },
    )
    assert calls(g, "p.Ledger.record(Item)") == set()
    assert all("may declare another write" in r for r in reasons(g, "p.Ledger.record(Item)")) and len(reasons(g, "p.Ledger.record(Item)")) == 3
    assert calls(g, "p.Use.f(Ledger,Item)") == set()


def test_the_members_of_object_enum_and_record_are_known_so_they_do_not_block(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                record Point(int x, int y) { int sum() { return x + y; } static Point origin() { return new Point(0, 0); } }
                enum Op { ADD, NEG; int run(int a) { return a; } static Op parse(String s) { return valueOf(s); } }
                class Use {
                    int f(Point p, Op o) { Point.origin(); o.run(1); Op.parse("ADD"); o.ordinal(); return p.sum() + p.x(); }
                }
                """
        },
    )
    assert calls(g, "p.Use.f(Point,Op)") == {"p.Point.origin()", "p.Op.run(int)", "p.Op.parse(String)", "p.Point.sum()"}
    assert reasons(g, "p.Use.f(Point,Op)") == []
    assert g.external_reasons["implicit enum member"] == 2 and g.external_reasons["implicit record accessor"] >= 1
    # an enum may overload one of Enum's names, and then a call of that name is not provably the repo's
    g2 = index(make_repo, {"p/B.java": "package p;\nenum Lvl { LOW; static Lvl valueOf(int i) { return LOW; } }\nclass Use2 { Lvl f() { return Lvl.valueOf(1); } }\n"})
    assert calls(g2, "p.Use2.f()") == set() and reasons(g2, "p.Use2.f()")


def test_an_anonymous_class_of_a_jdk_interface_may_inherit_the_name_it_calls(make_repo):
    # Executor is not one of the types whose members ripple knows, so it may declare any name
    g = index(
        make_repo,
        {
            "p/A.java": "package p;\nclass A {\n static void helper() {}\n"
            " void m() { java.util.concurrent.Executor r = new java.util.concurrent.Executor() { public void execute(Runnable x) { helper(); } }; r.execute(null); }\n}\n"
        },
    )
    assert calls(g, "p.A.m()$1.execute(Runnable)") == set()
    assert any("may be inherited from external base java.util.concurrent.Executor" in r for r in reasons(g, "p.A.m()$1.execute(Runnable)"))


def test_an_anonymous_class_of_a_jdk_interface_whose_members_are_known_inherits_only_those(make_repo):
    g = index(
        make_repo,
        {"p/A.java": "package p;\nclass A {\n static void helper() {}\n void m() { Runnable r = new Runnable() { public void run() { helper(); } }; r.run(); }\n}\n"},
    )
    assert calls(g, "p.A.m()$1.run()") == {"p.A.helper()"}
    assert any("a repo class that may be one declares run" in r for r in reasons(g, "p.A.m()"))


def test_a_name_no_repo_source_declares_cannot_run_repo_code_so_it_is_external(make_repo):
    g = index(make_repo, {"p/A.java": "package p;\nclass A { static void known(int a) {}\n void f(java.util.List<A> xs) { xs.get(0).known(1); xs.get(0).missing(); xs.get(0).known(1, 2); } }\n"})
    assert g.external_reasons.get("no repo method has this name and arity") == 2  # missing(), and known with two arguments
    assert len(g.unresolved) == 1  # known(1) may be A's: a call of a name the repo declares with that arity stays unresolved


def test_lombok_in_a_repo_turns_the_name_absence_rule_off(make_repo):
    files = {
        "p/Data.java": "package p;\npublic @interface Data {}\n",
        "p/Customer.java": "package p;\n@Data\npublic class Customer { String n; }\n",
        "p/Use.java": "package p;\nclass Use { void f(Customer c, java.util.List<Customer> xs) { c.getN(); xs.get(0).getN(); } }\n",
    }
    g = index(make_repo, files)
    assert [r for r in reasons(g, "p.Use.f(Customer,List)") if "Lombok" in r]  # c.getN(): not declared, and Customer is annotated
    assert g.external_reasons.get("no repo method has this name and arity") is None  # xs.get(0).getN() stays unresolved


# ---- constructors, dispatch, what is not a call ----------------------------------------------------------------------


def test_constructors_are_chosen_like_methods(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                class Item { Item(String n) {} Item(String n, int c) {} }
                class Sub extends Item { Sub() { super("x"); } Sub(int a) { this(); } }
                class Plain {}
                class Use { void f() { new Item("a"); new Item("a", 1); new Plain(); } }
                """
        },
    )
    assert edges(g, "p.Use.f()") == {"constructor p.Item.<init>(String)", "constructor p.Item.<init>(String,int)", "constructor p.Plain"}
    assert edges(g, "p.Sub.<init>()") == {"constructor p.Item.<init>(String)"}
    assert edges(g, "p.Sub.<init>(int)") == {"constructor p.Sub.<init>()"}


def test_dispatch_gets_override_edges_but_exact_receivers_do_not(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                interface Shape { double area(); }
                class Sq implements Shape { public double area() { return 1; } }
                class Circle implements Shape { public double area() { return 2; } }
                final class Fixed implements Shape { public double area() { return 3; } }
                class Use {
                    void f(Shape s) { s.area(); }
                    void g() { new Sq().area(); }
                    void h(Fixed x) { x.area(); }
                }
                """
        },
    )
    assert edges(g, "p.Use.f(Shape)") == {"call p.Shape.area()", "override p.Sq.area()", "override p.Circle.area()", "override p.Fixed.area()"}
    assert edges(g, "p.Use.g()") == {"call p.Sq.area()", "constructor p.Sq"}
    assert edges(g, "p.Use.h(Fixed)") == {"call p.Fixed.area()"}


def test_a_method_reference_is_not_a_call_and_a_lambdas_calls_belong_to_its_method(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": """\
                package p;
                import java.util.List;
                class A {
                    void show(String s) {}
                    void helper() {}
                    void m(List<String> xs) { xs.forEach(this::show); xs.forEach(s -> helper()); }
                }
                """
        },
    )
    assert calls(g, "p.A.m(List)") == {"p.A.helper()"}


def test_an_anonymous_class_of_a_repo_interface_is_made_by_its_new(make_repo):
    g = index(make_repo, {"p/A.java": "package p;\ninterface I { void f(); }\nclass A { void m() { I i = new I() { public void f() {} }; i.f(); } }\n"})
    assert edges(g, "p.A.m()") == {"constructor p.A.m()$1", "call p.I.f()", "override p.A.m()$1.f()"}


# ---- what the bytecode of a probe cannot tell: no override edge, and what is outside the repo ------------------------------


def test_a_package_private_method_is_not_overridden_from_another_package(make_repo):
    g = index(
        make_repo,
        {
            "p/a/Base.java": "package p.a;\npublic class Base { void m() {} public void run() { m(); } }\n",
            "p/b/Sub.java": "package p.b;\npublic class Sub extends p.a.Base { void m() {} }\n",
            "p/a/Same.java": "package p.a;\npublic class Same extends Base { void m() {} }\n",
        },
    )
    assert edges(g, "p.a.Base.run()") == {"call p.a.Base.m()", "override p.a.Same.m()"}  # p.b.Sub.m is another method


def test_a_call_on_a_value_of_a_final_jdk_class_is_outside_the_repo_but_one_on_object_or_an_interface_may_not_be(make_repo):
    g = index(
        make_repo,
        {
            "p/Thing.java": "package p;\npublic class Thing implements Runnable { public String toString() { return \"t\"; } public void run() {} }\n",
            "p/Use.java": """\
                package p;
                class Use {
                    void f(String s, Object o, Runnable r, StringBuilder b) {
                        s.toString();
                        b.toString();
                        o.toString();
                        r.run();
                        r.hashCode();
                    }
                }
                """,
        },
    )
    assert g.external_reasons["instance of external class (JDK)"] == 3  # String.toString, StringBuilder.toString, Runnable's hashCode
    assert sorted(u.reason.split(":")[1].strip() for u in g.unresolved if u.caller == "p.Use.f(String,Object,Runnable,StringBuilder)") == [
        "a repo class that may be one declares run",
        "a repo class that may be one declares toString",
    ]


def test_an_inherited_object_member_is_outside_the_repo_only_if_no_subtype_overrides_it(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": "package p;\nclass Plain {}\nclass Fancy extends Plain { public String toString() { return \"f\"; } }\n"
            "class Use { void f(Plain p) { p.toString(); p.hashCode(); } }\n"
        },
    )
    assert g.external_reasons == {"java.lang.Object member": 1}  # hashCode: no subtype of Plain declares it
    assert any("a subtype of it declares toString" in r for r in reasons(g, "p.Use.f(Plain)"))


def test_a_type_name_is_not_found_past_a_member_type_that_an_unseen_supertype_may_inherit(make_repo):
    g = index(
        make_repo,
        {
            "p/Item.java": "package p;\npublic class Item { void add() {} }\n",
            "p/Plain.java": "package p;\nimport java.io.Serializable;\npublic class Plain implements Serializable, Comparable<Plain> {\n public int compareTo(Plain o) { return 0; }\n void f(Item i) { i.add(); }\n}\n",
            "p/Mine.java": "package p;\npublic class Mine extends java.util.AbstractMap<String, String> {\n public java.util.Set<java.util.Map.Entry<String, String>> entrySet() { return null; }\n void f(Item i) { i.add(); }\n}\n",
        },
    )
    assert edges(g, "p.Plain.f(Item)") == {"call p.Item.add()"}  # Serializable and Comparable declare no member type: known
    assert edges(g, "p.Mine.f(Item)") == set()  # AbstractMap's Map.Entry could as well have been another Item
    assert any("may be a member type that p.Mine inherits from java.util.AbstractMap" in r for r in reasons(g, "p.Mine.f(Item)"))


def test_a_private_field_of_a_supertype_is_not_inherited(make_repo):
    g = index(
        make_repo,
        {
            "p/Outer.java": """\
                package p;
                public class Outer {
                    static class Money { void add(int x) {} }
                    static class Item { void add(int x) {} }
                    Money total = new Money();
                    static class A { private Item total = new Item(); protected Item shared = new Item(); }
                    class B extends A {
                        void f() { total.add(1); shared.add(2); }
                    }
                }
                """
        },
    )
    assert calls(g, "p.Outer.B.f()") == {"p.Outer.Money.add(int)", "p.Outer.Item.add(int)"}


# ---- the profile and the cache ------------------------------------------------------------------------------------------


def test_java_indexing_leaves_the_python_and_typescript_cache_keys_alone():
    from ripple.cache import profile_key
    from ripple.profile import PYTHON, TYPESCRIPT

    assert profile_key(PYTHON) == ""  # the Python cache is the one that has always been written
    assert profile_key(TYPESCRIPT).startswith("typescript-")  # its digest is pinned by the fields, which Java added none to
    assert profile_key(JAVA).startswith("java-") and profile_key(JAVA) != profile_key(TYPESCRIPT)


# ---- what the second review found: comments, inherited implementations, marker interfaces, unreadable headers -------


def test_a_comment_inside_an_argument_list_is_no_argument(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": "package p;\nclass A {\n void f(int a) {}\n void f(int a, int b) {}\n"
            " void m() { f(1 /* one */); f(/* none */ 3); f(1, // two\n 2); new A(4 /* four */); }\n A(int a) {}\n A(int a, int b) {}\n}\n"
        },
    )
    assert calls(g, "p.A.m()") == {"p.A.f(int)", "p.A.f(int,int)"}
    assert edges(g, "p.A.m()") >= {"constructor p.A.<init>(int)"} and "constructor p.A.<init>(int,int)" not in edges(g, "p.A.m()")
    assert reasons(g, "p.A.m()") == []


def test_a_subtype_that_inherits_its_implementation_from_an_unrelated_class_gets_an_override_edge(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": "package p;\ninterface I { void m(); }\nclass A { public void m() {} }\nclass B extends A implements I {}\n"
            "class C { void f(I i) { i.m(); } }\n"
        },
    )
    assert edges(g, "p.C.f(I)") == {"call p.I.m()", "override p.A.m()"}


def test_a_marker_interface_declares_nothing_so_it_blocks_no_call(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": "package p;\nimport java.io.Serializable;\nclass Data implements Serializable, Cloneable { void use() {} }\n"
            "class Res implements AutoCloseable { void use() {} public void close() {} public void close(int n) {} }\n"
            "class Lib implements java.util.concurrent.Executor { void use() {} public void execute(Runnable r) {} }\n"
            "class C { void f(Data d, Res r, Lib l) { d.use(); r.use(); r.close(1); r.close(); l.use(); } }\n"
        },
    )
    # AutoCloseable declares close() and nothing else: close(int) is not its, and close() is the one Res overrides
    assert calls(g, "p.C.f(Data,Res,Lib)") == {"p.Data.use()", "p.Res.use()", "p.Res.close(int)", "p.Res.close()"}
    assert len(reasons(g, "p.C.f(Data,Res,Lib)")) == 1 and "Executor" in reasons(g, "p.C.f(Data,Res,Lib)")[0]  # only the type ripple does not know


def test_a_method_whose_header_the_parser_cannot_read_decides_no_call_of_its_name(make_repo):
    g = index(
        make_repo,
        {
            "p/A.java": "package p;\n@java.lang.annotation.Target(java.lang.annotation.ElementType.TYPE_USE) @interface Nullable {}\n"
            "class U { static String name(Class<?> c, String m, Class<?> @Nullable... params) { return m; } static String name(String a, String b, String c) { return a; } }\n"
            'class C { String f(Class<?> c) { return U.name(c, "x"); } }\n'
        },
    )
    assert calls(g, "p.C.f(Class)") == set()
    assert any("could not be read" in r for r in reasons(g, "p.C.f(Class)"))
