"""The .java files of a repo that ripple does not read whole (package-info.java, build output, a file in a directory that is not its
package's, a file the parser met an error in), and what a call may conclude about code it cannot see.

Two things are pinned here. (1) What such a file may declare is read from its TEXT (java_indexer.scan_unread) and is exact enough that
an ordinary file costs nothing: the prose of a Javadoc declares no type, a nested type is no type of the package, a module-info.java
declares nothing, a byte-identical copy of a file that was read declares what that file does, a method the parser did read in a file
it read in part is not unseen. (2) Every conclusion "no repo code runs here" that rests on the ABSENCE of a method (a call with no repo
method of the name, a value of a type from outside the repo that a repo type may be, a method inherited from outside the repo that a
subtype may declare) asks one question, JavaResolver.unseen_method(name, arguments), whatever the call is and however it came to be
unresolved: each route to an external verdict has a test below that fails when the question is not asked there.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_java")

from ripple.indexer import build_graph, walk_repo  # noqa: E402
from ripple import java_indexer  # noqa: E402
from ripple.java_indexer import Declarations, load_parser  # noqa: E402
from ripple.java_resolver import JavaResolver  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

MARKERS = {"pom.xml": "<project/>"}


def declared(make_repo, files: dict) -> tuple[Declarations, JavaResolver]:
    """The declarations the way index_files makes them: the files the walk hands over are read, the others are noted."""
    root = make_repo(files).resolve()
    declarations, parser = Declarations(), load_parser()
    paths, _ = walk_repo(root, JAVA)
    for path in paths:
        declarations.add_file(path, root, parser)
    declarations.note_unread_sources(root, paths)
    declarations.finish()
    return declarations, JavaResolver(declarations, JAVA).run()


def graph(make_repo, files: dict):
    return build_graph(make_repo({**MARKERS, **files}), JAVA)


def unseen_nothing(declarations: Declarations) -> bool:
    return (
        not any(declarations.unseen_names.values())
        and not declarations.unseen_methods
        and not declarations.unseen_packages
        and not declarations.unseen_anywhere
    )


# ---- an ordinary file that declares no type of a name, and no method of it, costs nothing ---------------------------------------


def test_the_prose_of_a_package_info_javadoc_declares_nothing(make_repo):
    # "this class Timer", "the class provides": a word after `class` in a comment is no type
    javadoc = "/**\n * This class Timer is not declared here; the class provides, and the interface Handler, enum Mode and record Pair too.\n */\n"
    declarations, _ = declared(make_repo, {"p/package-info.java": f"{javadoc}@Deprecated\npackage p;\n", "p/Use.java": "package p;\nclass Use {}\n"})
    assert unseen_nothing(declarations)


def test_a_module_info_that_says_the_word_package_in_a_comment_leaves_no_package_unknown(make_repo):
    # the old reading took the word `package` in a file with no package line as an unreadable package declaration, and then every
    # name of every package that an on-demand import might supply was unresolved (junit5 has such a module-info.java)
    declarations, _ = declared(
        make_repo,
        {"module-info.java": "// the package p is exported\nmodule m {\n  exports p;\n  uses p.Service;\n}\n", "p/Use.java": "package p;\nclass Use {}\n"},
    )
    assert unseen_nothing(declarations)


def test_a_file_the_parser_read_in_part_with_the_word_package_in_a_comment_and_no_package_line_leaves_no_package_unknown(make_repo):
    declarations, _ = declared(make_repo, {"Broken.java": "// the package of this file is the default one\nclass Broken { void m(int a { } }\n"})
    assert "Broken.java" in declarations.errors[0]
    assert not declarations.unseen_anywhere


def test_only_a_top_level_type_is_a_type_of_the_package(make_repo):
    # `Builder` and `Entry` of a nested class are no type of p: a `Builder` that an on-demand import supplies is not hidden by them
    source = "package p;\nclass Outer {\n  static class Builder { void m() {} }\n  interface Entry {}\n}\nenum Mode { A, B }\n"
    declarations, _ = declared(make_repo, {"p/package-info.java": source})
    assert declarations.unseen_names["p"] == {"Outer", "Mode"}


def test_where_the_braces_do_not_balance_every_type_name_is_unseen(make_repo):
    # no depth says what is top level, so the nested names are taken too (a file javac compiles balances, and this one may not)
    assert java_indexer.scan_unread("package p;\nclass Outer {\n  static class Builder {\n").types == {"Outer", "Builder"}
    assert java_indexer.scan_unread("package p;\nclass Outer { }\n}\nclass Builder { }\n").types == {"Outer", "Builder"}
    assert java_indexer.scan_unread("package p;\nclass Outer { static class Builder { } }\n").types == {"Outer"}


def test_a_word_in_a_string_or_a_text_block_or_a_character_declares_nothing():
    # each literal holds `{` or `class Fake {`: read as code it would open a body, and the file would not balance
    source = (
        'package p;\nclass Real { String a = "class Fake {"; String b = """\n  class Fake2 {\n  """; char c = \'{\'; char d = \'"\'; }\n'
        "class Last { }\n"
    )
    assert java_indexer.scan_unread(source).types == {"Real", "Last"}


def test_a_type_name_that_starts_with_a_letter_that_is_not_ascii_is_read():
    assert java_indexer.scan_unread("package p;\nclass Ünï { }\n").types == {"Ünï"}


def test_a_method_is_declared_where_a_name_follows_a_type_and_no_other_place():
    source = (
        "package p;\n"
        "class A {\n"
        "  void real(int a) { list.size(); new Foo(x); return bar(1); other(2); if (z) { } for (;;) { } Foo.of(3); }\n"
        "  <T> java.util.List<T> generic(java.util.Map<String, T> m, T... rest) { return null; }\n"
        "  int[] array() { return null; }\n"
        "  String name();\n"
        "}\n"
    )
    assert java_indexer.scan_unread(source).methods == {("real", 1, False), ("generic", 2, True), ("array", 0, False), ("name", 0, False)}


def test_what_a_unicode_escape_spells_is_what_is_read():
    # JLS 3.3: `class` is `class`, and `Timer` is Timer (the escape is read before anything is a token)
    assert java_indexer.scan_unread("package p;\n\\u0063lass \\u0054imer { void f\\u0028int a) { } }\n").types == {"Timer"}
    assert java_indexer.scan_unread("package p;\n\\u0063lass \\u0054imer { void f\\u0028int a) { } }\n").methods == {("f", 1, False)}
    assert java_indexer.scan_unread('package p;\nclass A { String s = "\\\\u0054imer"; }\n').types == {"A"}  # `\\u` is no escape


# ---- a copy of what was read, and a method that was read, are not unseen -------------------------------------------------------


def test_a_byte_identical_copy_of_a_file_that_was_read_in_build_output_declares_nothing_new(make_repo):
    source = "package p;\nclass Timer { void cancelAll() {} }\n"
    declarations, _ = declared(make_repo, {**MARKERS, "src/p/Timer.java": source, "target/classes-copy/p/Timer.java": source})
    assert unseen_nothing(declarations)
    declarations, _ = declared(make_repo, {**MARKERS, "src/p/Timer.java": source, "target/classes-copy/p/Timer.java": source + "// changed\n"})
    assert declarations.unseen_names["p"] == {"Timer"} and "cancelAll" in declarations.unseen_methods


def test_the_class_a_copy_declares_is_not_unseen_to_itself(make_repo):
    # `Timer` is a type of p that ripple read: a copy under target/ must not make the name refused in p, where it is the read type
    source = "package p;\nclass Timer { void cancelAll() {} }\n"
    files = {**MARKERS, "src/p/Timer.java": source, "target/gen/p/Timer.java": source, "src/p/Use.java": "package p;\nimport java.util.*;\nclass Use { void f(Timer t) { t.cancelAll(); } }\n"}
    g = build_graph(make_repo(files), JAVA)
    assert {e.callee for e in g.edges if e.caller == "p.Use.f(Timer)"} == {"p.Timer.cancelAll()"}


def test_every_method_of_a_file_the_parser_read_in_part_is_unseen_even_those_it_saw(make_repo):
    # (it used to subtract the methods the parser saw: after an error the owner it recovered is no evidence, see test_java_unread_scan.py)
    source = "package p;\nclass A {\n  void ok(int a) {}\n  void bad(String s, int... x) { int y = ; }\n  void after(int q) {}\n}\n"
    declarations, _ = declared(make_repo, {"p/A.java": source})
    assert declarations.errors == ["p/A.java"]
    assert {k: sorted(v) for k, v in declarations.unseen_methods.items()} == {"ok": [(1, False)], "bad": [(2, True)], "after": [(1, False)]}


def test_a_file_read_in_part_leaves_unseen_the_method_it_declares_that_the_parser_did_not_see(make_repo):
    # `@@ void lost(String s)` is read as a constructor of A, so no method `lost` is among those read; `ok` and `tail` are
    source = "package p;\nclass A {\n  void ok(int a) {}\n  @@ void lost(String s) {}\n  void tail(long q) {}\n}\n"
    declarations, _ = declared(make_repo, {"p/A.java": source})
    assert sorted(m.id for m in declarations.methods.values() if m.name != "<init>") == ["p.A.ok(int)", "p.A.tail(long)"]
    assert {k: v for k, v in declarations.unseen_methods.items()} == {"ok": {(1, False)}, "lost": {(1, False)}, "tail": {(1, False)}}


# ---- every route to an external verdict asks unseen_method ----------------------------------------------------------------------


def test_route_a_call_no_repo_method_has_the_name_of_is_not_external_when_an_unread_file_declares_it(make_repo):
    g = graph(
        make_repo,
        {
            "src/p/Outer.java": "package p;\nimport java.util.*;\nclass Outer { void f(Timer t) { t.cancelAll(); } }\n",
            "target/generated-sources/p/Timer.java": "package p;\npublic class Timer { public void cancelAll() {} }\n",
        },
    )
    assert [u for u in g.unresolved if u.caller == "p.Outer.f(Timer)" and u.text == "t.cancelAll"]  # declared in a file ripple did not read
    assert g.external_calls == 0


def test_route_a_call_no_repo_method_has_the_name_of_is_external_when_no_unread_file_does(make_repo):
    g = graph(
        make_repo,
        {
            "src/p/Outer.java": "package p;\nclass Outer { void f(java.util.List<String> l) { l.size(); } }\n",
            "target/generated-sources/p/Timer.java": "package p;\npublic class Timer { public void cancelAll() {} }\n",
        },
    )
    assert g.external_calls >= 1 and not [u for u in g.unresolved if u.text == "l.size"]


def test_route_a_value_of_an_external_type_may_be_an_instance_of_a_type_an_unread_file_declares(make_repo):
    # javac: `r.run()` runs Task.run when r is a Task. The external verdict "instance of external class Runnable" asked only the read
    # sources
    g = graph(
        make_repo,
        {
            "src/p/Use.java": "package p;\nclass Use { void f(Runnable r) { r.run(); } }\n",
            "target/generated-sources/p/Task.java": "package p;\npublic class Task implements Runnable { public void run() {} }\n",
        },
    )
    assert [u for u in g.unresolved if u.caller == "p.Use.f(Runnable)" and u.text == "r.run"]
    assert g.external_calls == 0


def test_route_an_external_value_has_no_instance_in_an_unread_file_that_declares_another_arity(make_repo):
    g = graph(
        make_repo,
        {
            "src/p/Use.java": "package p;\nclass Use { void f(Runnable r) { r.run(); } }\n",
            "target/generated-sources/p/Task.java": "package p;\npublic class Task implements Runnable { public void run(int n) {} }\n",
        },
    )
    assert g.external_calls == 1 and not g.unresolved


BASES = {
    "src/p/A.java": "package p;\nclass A extends Thread { void g() { setName(\"x\"); } }\nclass Use { void f(A a) { a.setName(\"x\"); } }\n",
}


def test_route_a_method_inherited_from_outside_the_repo_may_be_overridden_in_an_unread_subtype(make_repo):
    # `a.setName("x")`: A inherits it from Thread, and a subtype of A that a generated file declares overrides it
    files = {**BASES, "target/generated-sources/p/B.java": "package p;\npublic class B extends A { public void setName(String s) {} }\n"}
    g = graph(make_repo, files)
    assert [u for u in g.unresolved if u.caller == "p.Use.f(A)" and u.text == "a.setName"]


def test_route_an_unqualified_call_of_a_method_inherited_from_outside_the_repo_may_be_overridden_in_an_unread_subtype(make_repo):
    files = {**BASES, "target/generated-sources/p/B.java": "package p;\npublic class B extends A { public void setName(String s) {} }\n"}
    g = graph(make_repo, files)
    assert [u for u in g.unresolved if u.caller == "p.A.g()" and u.text == "setName"]


def test_route_a_method_inherited_from_outside_the_repo_stays_external_where_no_unread_file_declares_it(make_repo):
    files = {**BASES, "target/generated-sources/p/B.java": "package p;\npublic class B extends A { public void other() {} }\n"}
    g = graph(make_repo, files)
    assert not [u for u in g.unresolved if u.text in ("a.setName", "setName")]
    assert g.external_calls == 2


def test_an_enum_a_record_and_a_final_class_have_no_subtype_in_an_unread_file(make_repo):
    files = {
        "src/p/E.java": "package p;\nenum E { X }\nrecord R(int a) { }\nfinal class F extends Thread { }\nclass Use { void f(F f) { E.X.name(); f.setName(\"x\"); } }\n",
        "target/generated-sources/p/Other.java": "package p;\npublic class Other { public String name() { return \"\"; } public void setName(String s) {} }\n",
    }
    g = graph(make_repo, files)
    assert not [u for u in g.unresolved if u.caller == "p.Use.f(F)"], g.unresolved
    assert g.external_calls == 2


def test_an_unreadable_file_makes_every_absence_of_a_method_name_unproven(make_repo):
    root = make_repo({**MARKERS, "src/p/Use.java": "package p;\nclass Use { void f(java.util.List<String> l) { l.size(); } }\n"}).resolve()
    declarations, parser = Declarations(), load_parser()
    paths, _ = walk_repo(root, JAVA)
    for path in paths:
        declarations.add_file(path, root, parser)
    declarations.note_unseen_file(root / "src" / "Vanished.java")  # a file the walk saw and that went away (OSError)
    declarations.finish()
    resolver = JavaResolver(declarations, JAVA).run()
    assert sum(resolver.external.values()) == 0
    assert [u for u in resolver.unresolved if u.text == "l.size"]
