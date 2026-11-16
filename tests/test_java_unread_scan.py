"""What the text of a .java file that ripple did not read whole may declare (java_indexer.scan_unread), and what a call may conclude.

The scan errs toward MORE names. A method name in such a file is taken wherever the syntax allows a declaration (a declared method has
its result type before its name: JLS 8.4), by an identifier that is every run of characters that is no blank, separator, operator or
quote (JLS 3.8 allows `$`, currency signs, connector punctuation, combining marks and every letter of Unicode, and a list of them goes
out of date), after the Unicode escapes are read as javac reads them (JLS 3.3) and the bytes are decoded so that no byte sequence
hides a name (UTF-8, else ISO-8859-1; a byte-order mark names UTF-16 and UTF-32). The arity narrows a refusal only where the parameter
list is read with certainty: closed, no receiver parameter (JLS 8.4.1), and a varargs one takes `n >= k - 1` arguments.

Each graph case is a call on a value of a JDK type (`java.util.List`) of a method `m` that no read source declares, with a file under
target/gen that declares `m`: javac compiles the call to the unread class's `m` whenever the object is one of its instances (a
`Gen implements List` would be), so "no repo code runs here" is not proven and the call is unresolved, not external. The same call
with a name no file declares stays external, so the refusals are not for nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_java")

from ripple import java_indexer  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.java_indexer import scan_unread  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

MARKERS = {"pom.xml": "<project/>"}


def write(root, files: dict):
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source.encode("utf-8"))
    return root


def site(tmp_path, declaration: str, call: str, extra: str = ""):
    """Index a repo where Use.go makes `call` on a java.util.List, and an unread file declares `declaration` (and `extra`, outside the class)."""
    write(
        tmp_path,
        {
            **MARKERS,
            "src/main/java/p/Use.java": f"package p;\nclass Use {{ void go(java.util.List<String> l, Object o) {{ {call}; }} }}\n",
            "target/gen/p/Gen.java": f"package p;\npublic class Gen {{ {declaration} }}\n{extra}",
        },
    )
    g = build_graph(tmp_path, JAVA)
    unresolved = [u for u in g.unresolved if u.caller == "p.Use.go(List,Object)"]
    return unresolved, g.external_calls


def refused(tmp_path, declaration: str, call: str, extra: str = "") -> bool:
    unresolved, external = site(tmp_path, declaration, call, extra)
    assert bool(unresolved) != bool(external), (unresolved, external)  # (the one call is one or the other)
    return bool(unresolved)


# ---- the names of a method: every contextual keyword, and the characters an identifier may have ---------------------------------


CONTEXTUAL = ["record", "yield", "var", "sealed", "permits", "module", "open", "opens", "requires", "exports", "provides", "uses", "to", "with", "transitive", "when"]


@pytest.mark.parametrize("word", CONTEXTUAL)
def test_a_method_named_with_a_contextual_keyword_is_declared(tmp_path, word):
    # javac 21 accepts `public void record(Object o) {}` and `public static void yield() {}` (Thread.yield exists), and a call is
    # invokevirtual on the class
    assert java_indexer.scan_unread(f"package p; class A {{ void {word}(int x) {{}} }}").methods == {(word, 1, False)}
    assert refused(tmp_path, f"public void {word}(Object o) {{}}", f"l.{word}(o)")


def test_a_contextual_keyword_that_starts_a_type_is_still_a_type_and_a_method_beside_it_a_method():
    scan = scan_unread("package p; record R(int a, String b) { void record(int x) {} }\nsealed interface S permits R { }\n")
    assert scan.types == {"R", "S"}
    assert scan.methods == {("a", 0, False), ("b", 0, False), ("record", 1, False)}  # the accessors of the components, and the method


def test_a_record_declares_the_accessor_of_each_component(tmp_path):
    # `record Box(Object get) implements java.util.function.Supplier<Object>`: `get()` is the accessor, and a call through the interface
    # runs it (JLS 8.10.3)
    write(
        tmp_path,
        {
            **MARKERS,
            "src/main/java/p/Use.java": "package p;\nclass Use { void go(java.util.function.Supplier<Object> s) { s.get(); } }\n",
            "target/gen/p/Box.java": "package p;\npublic record Box(Object get) implements java.util.function.Supplier<Object> { }\n",
        },
    )
    g = build_graph(tmp_path, JAVA)
    assert [u for u in g.unresolved if u.text == "s.get"] and g.external_calls == 0


CHARACTERS = {
    "currency": "pay\u20ac",  # a currency sign
    "combining": "rece\u0301ive",  # e + a combining acute accent (Mn)
    "devanagari": "\u0928\u093e\u092e",  # letters and a vowel sign (Mc)
    "supplementary": "\U0001d49cx",  # MATHEMATICAL SCRIPT CAPITAL A, a letter outside the BMP
    "connector": "a\u203fb",  # a connector punctuation mark (Pc) inside a name
    "dollar": "$gen",
    "underscore": "_gen",
    "ignorable": "fo\u00ado",  # a soft hyphen: JLS 3.8, identifiers that differ only by ignorable characters are the same
}


@pytest.mark.parametrize("label", CHARACTERS)
def test_a_method_named_with_a_character_that_is_not_in_python_s_w_is_declared(tmp_path, label):
    declared = CHARACTERS[label]
    called = "foo" if label == "ignorable" else declared
    assert [(n, k) for n, k, _ in scan_unread(f"package p; class A {{ void {declared}() {{}} }}").methods] == [(called, 0)]
    if label == "currency":
        return  # (tree-sitter-java does not take a currency sign in an identifier, so a call written with one is not a site ripple sees)
    assert refused(tmp_path, f"public void {declared}() {{}}", f"l.{called}()")


def test_a_type_named_with_such_characters_is_a_type_of_the_package():
    assert scan_unread("package p;\nclass Pr\u20acce { }\nclass Caf\u00e9 { }\nclass \U0001d49cA { }\n").types == {"Pr\u20acce", "Caf\u00e9", "\U0001d49cA"}


def test_a_package_written_with_such_characters_is_read():
    assert scan_unread("package p\u20ac.q\u0301;\nclass A { }\n").package == "p\u20ac.q\u0301"


# ---- the arity ------------------------------------------------------------------------------------------------------------------


def test_a_receiver_parameter_is_no_argument_and_the_arity_is_not_read(tmp_path):
    # JLS 8.4.1: `void self(Gen this)` takes no argument, and `void sum(Gen Gen.this, int a)` takes one. The count would be one more.
    assert scan_unread("package p; class A { void self(A this) {} void sum(A A.this, int a) {} void k(@Deprecated A this, int x) {} }").methods == {
        ("self", None, False),
        ("sum", None, False),
        ("k", None, False),
    }
    assert refused(tmp_path, "public void self(Gen this) {}", "l.self()")


def test_a_receiver_parameter_with_another_parameter_is_refused_at_its_real_arity(tmp_path):
    assert refused(tmp_path, "public void sum(Gen Gen.this, int a) {}", "l.sum(1)")


def test_a_shift_or_a_comparison_in_an_annotation_value_does_not_change_the_count(tmp_path):
    # `<` inside an annotation's parentheses is an operator: it opened an angle bracket and the comma after it was not counted
    shift = scan_unread("package p; class A { void m(@B(v = 1 << 3) int a, int b) {} void n(@B(v = 1 < 2) int a, int b) {} }")
    assert shift.methods == {("m", 2, False), ("n", 2, False)}
    extra = "@interface B { int v(); }\n"
    assert refused(tmp_path, "public void m(@B(v = 1 << 3) int a, int b) {}", "l.m(1, 2)", extra)


def test_a_generic_parameter_list_counts_its_commas_outside_the_type_arguments():
    scan = scan_unread("package p; class A { <K, V> void m(java.util.Map<K, java.util.List<V>> a, java.util.function.BiFunction<K, V, V> b, int c) {} }")
    assert scan.methods == {("m", 3, False)}


def test_the_arity_of_a_declaration_read_with_certainty_narrows_the_refusal(tmp_path):
    # (so the refusals are not for nothing) `void one(int a)` takes one argument: a call with none or two is not a call of it
    assert refused(tmp_path, "public void one(int a) {}", "l.one(1)")
    assert not refused(tmp_path, "public void one(int a) {}", "l.one()")


def test_a_varargs_method_takes_every_number_of_arguments_from_one_less_than_its_parameters(tmp_path):
    # javac: `m()` and `m("a", "b")` both call `m(String...)`. (L4: `n == k or (varargs and n >= k - 1)` lost its second clause unnoticed.)
    assert scan_unread("package p; class A { void many(String... a) {} void two(int a, int... b) {} }").methods == {("many", 1, True), ("two", 2, True)}
    assert refused(tmp_path, "public void many(String... a) {}", "l.many()")
    other = tmp_path / "other"
    other.mkdir()
    assert refused(other, "public void many(String... a) {}", "l.many(\"a\", \"b\")")
    third = tmp_path / "third"
    third.mkdir()
    assert refused(third, "public void two(int a, int... b) {}", "l.two(1)")
    fourth = tmp_path / "fourth"
    fourth.mkdir()
    assert not refused(fourth, "public void two(int a, int... b) {}", "l.two()")  # (needs at least the first)


def test_a_list_that_is_not_closed_has_no_arity_and_refuses_every_call(tmp_path):
    assert scan_unread("package p; class A { void m(int a, ").methods == {("m", None, False)}
    assert scan_unread("package p; class A { void m(int a, int b } ").methods == {("m", None, False)}


def test_a_name_before_an_open_parenthesis_that_is_not_after_a_type_is_a_call_and_declares_nothing():
    source = (
        "package p; class A { int f; void a() { b(); x.c(); y = d(1); e(f(2)); int g = (int) h(3); i = j ? k(4) : l(5);"
        " new M(6); return n(7); throw o(8); else p(9); if (q(10)) { } for (r(11);;) { } s -> t(12); } }"
    )
    assert scan_unread(source).methods == {("a", 0, False)}


def test_a_word_that_cannot_be_a_method_name_is_not_noted():
    assert scan_unread("package p; class A { void a() { if (x) { } while (y) { } switch (z) { } synchronized (w) { } try (V v = null) { } catch (E e) { } } }").methods == {
        ("a", 0, False)
    }


def test_a_declaration_after_a_token_the_scan_does_not_know_is_noted_with_no_arity():
    # whatever stands before a name and an open parenthesis that is neither a result type nor an expression is read as a possible
    # declaration of unknown arity: the direction of the error is toward more names
    assert scan_unread("package p; class A { \\ weird(1, 2); }").methods == {("weird", None, False)}


def test_the_methods_of_an_anonymous_class_an_enum_constant_body_and_an_interface_are_declared():
    source = (
        "package p; interface I { void a(); default int b(int x) { return x; } static I c() { return null; } }\n"
        "enum E { X { void d() {} }, Y; void e(int a) {} }\n"
        "class T { Object o = new Object() { public String toString() { return \"\"; } }; @interface Q { String value() default \"\"; } }\n"
    )
    names = {n for n, _, _ in scan_unread(source).methods}
    assert names == {"a", "b", "c", "d", "e", "toString", "value"}


# ---- text that is not Java source in the usual way -------------------------------------------------------------------------------


def test_a_non_utf_8_source_loses_no_name():
    source = "package p;\npublic class Gr\u00f6\u00dfe { public int gr\u00f6\u00dfe() { return 1; } }\n".encode("latin-1")
    scan = scan_unread(java_indexer.decode_source(source))
    assert scan.types == {"Gr\u00f6\u00dfe"} and scan.methods == {("gr\u00f6\u00dfe", 0, False)}


def test_the_scan_declares_every_method_the_parser_reads_in_the_java_fixtures():
    """The oracle for the scan's shape rules: on every Java file of bench/fixtures/java that the parser reads without an error, each
    method it declares (and each accessor of a record) is among the names the text scan notes for that file, with its arity (or none).
    The same check over gson, commons-lang, junit5, okhttp, auto, petclinic and antennapod (3,785 files, 37,567 methods) missed none."""
    from pathlib import Path

    fixtures = Path(__file__).resolve().parent.parent / "bench" / "fixtures" / "java"
    parser = java_indexer.load_parser()
    checked = 0
    for path in sorted(fixtures.rglob("*.java")):
        data = path.read_bytes()
        if parser.parse(data).root_node.has_error:
            continue
        declarations = java_indexer.Declarations()
        declarations.add_file(path, fixtures, parser)
        scan = scan_unread(java_indexer.decode_source(data))
        for t in declarations.every_type:
            for found in t.methods.values():
                for m in found:
                    checked += 1
                    assert any(n == m.name and (k is None or (k == len(m.params) and va == m.varargs)) for n, k, va in scan.methods), (str(path), m.id)
            for _, component in t.components:
                checked += 1
                assert any(n == component and k in (0, None) for n, k, _ in scan.methods), (str(path), t.fqn, component)
    assert checked > 1000


# ---- L1: an enum whose file was read in part ------------------------------------------------------------------------------------


def test_an_enum_read_whole_has_no_subtype_in_an_unread_file(tmp_path):
    write(
        tmp_path,
        {
            **MARKERS,
            "src/main/java/p/E.java": "package p;\nenum E implements Iterable<String> {\n  A, B;\n  public java.util.Iterator<String> iterator() { return null; }\n}\n",
            "src/main/java/p/Use.java": "package p;\nclass Use { void go(E e) { e.forEach(s -> {}); } }\n",
            "target/gen/p/Other.java": "package p;\npublic class Other { public void forEach(java.util.function.Consumer<? super String> c) {} }\n",
        },
    )
    g = build_graph(tmp_path, JAVA)
    assert not [u for u in g.unresolved if u.text == "e.forEach"] and g.external_calls == 1


def test_an_enum_read_in_part_may_have_a_constant_body_the_parser_lost(tmp_path):
    """javac 21 accepts `A \\u007b public void forEach(...) { ... } \\u007d` (the escaped braces are the constant's body, which is
    `E$1.forEach`, repo code); tree-sitter does not read the escape as a brace and loses the body. `e.forEach(s -> {})` on an `E` may
    run it, so it is not external."""
    write(
        tmp_path,
        {
            **MARKERS,
            "p/E.java": (
                "package p;\nimport java.util.Iterator;\nimport java.util.function.Consumer;\n"
                "enum E implements Iterable<String> {\n"
                "    A \\u007b\n        public void forEach(Consumer<? super String> c) { System.out.println(\"repo code\"); }\n    \\u007d,\n    B;\n"
                "    public Iterator<String> iterator() { return java.util.Collections.emptyIterator(); }\n}\n"
            ),
            "p/Use.java": "package p;\nclass Use { void go(E e) { e.forEach(s -> {}); } }\n",
        },
    )
    g = build_graph(tmp_path, JAVA)
    assert "p/E.java" in g.parse_errors
    assert [u for u in g.unresolved if u.text == "e.forEach"]
    assert not [reason for reason in g.external_reasons if "inherited from an external supertype" in reason]


def test_a_final_class_and_a_record_read_in_part_may_have_a_subtype_in_a_file_ripple_did_not_read(tmp_path):
    write(
        tmp_path,
        {
            **MARKERS,
            "src/main/java/p/F.java": "package p;\nfinal class F extends Thread { void bad( { } }\n",
            "src/main/java/p/Use.java": 'package p;\nclass Use { void go(F f) { f.setName("x"); } }\n',
            "target/gen/p/Other.java": "package p;\npublic class Other { public void setName(String s) {} }\n",
        },
    )
    g = build_graph(tmp_path, JAVA)
    assert "src/main/java/p/F.java" in g.parse_errors
    assert [u for u in g.unresolved if u.text == "f.setName"]


# ---- L2: a method the parser saw in a file it read in part is not evidence --------------------------------------------------------


def test_a_method_the_parser_attached_to_the_wrong_owner_after_an_error_is_still_unseen(tmp_path):
    """javac 21 accepts `class Impl impl\\u0065ments Supplier<String> { public String get() { ... } }`: `Use.go(Supplier)` called with
    `new Impl()` runs `Impl.get`. Tree-sitter recovers a phantom top-level `Supplier` with `get()` and no supertype, and `get` was
    taken off the unseen names as one the parser saw: the call `s.get()` was then 'instance of external class (JDK)'."""
    write(
        tmp_path,
        {
            **MARKERS,
            "p/Impl.java": 'package p;\nimport java.util.function.Supplier;\nclass Impl impl\\u0065ments Supplier<String> {\n    public String get() { System.out.println("repo code"); return "x"; }\n}\n',
            "p/Use.java": "package p;\nimport java.util.function.Supplier;\nclass Use {\n    void go(Supplier<String> s) { s.get(); }\n    void make() { go(new Impl()); }\n}\n",
        },
    )
    g = build_graph(tmp_path, JAVA)
    assert "p/Impl.java" in g.parse_errors
    assert [u for u in g.unresolved if u.text == "s.get"]
    assert "instance of external class (JDK)" not in g.external_reasons


def test_every_method_text_of_a_file_read_in_part_is_unseen(tmp_path):
    source = "package p;\nclass A {\n  void ok(int a) {}\n  void bad(String s, int... x) { int y = ; }\n  void after(int q) {}\n}\n"
    write(tmp_path, {**MARKERS, "p/A.java": source})
    declarations = java_indexer.Declarations()
    parser = java_indexer.load_parser()
    path = tmp_path / "p" / "A.java"
    declarations.add_file(path, tmp_path, parser)
    declarations.finish()
    assert declarations.errors == ["p/A.java"] and declarations.partial_files == {"p/A.java"}
    assert {name: sorted(v) for name, v in declarations.unseen_methods.items()} == {
        "ok": [(1, False)],
        "bad": [(2, True)],
        "after": [(1, False)],
    }
