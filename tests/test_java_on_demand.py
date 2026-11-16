"""A simple name that an on-demand import supplies (`import java.util.*;`), and what ripple may conclude from the JDK table about it.

The rule is STRICT. A name an on-demand import of a JDK package might supply is `?.Name`, a type outside the repo of a package that
is not known, and no fact about the JDK type is used for it: a package is not closed (a jar on the classpath, or generated code,
can put a type of the same name into the file's own package or into an imported repo package, and by JLS 6.4.1 that type shadows
every on-demand import), so "java.util.Timer has no foo" is an absence fact that may be about the wrong type. Every test below is
one way a reading that took the JDK type for the name makes a wrong edge or a wrong "external", and names the reading it refuses.
The names java.lang supplies keep the reading they had before (a known assumption, stated in the README's Limits), and these tests
pin that it did not change. tests/test_java_probes.py holds the shapes javac can compile, with javac's answer beside each
(bench/fixtures/java/probes/ondemand). No JDK is needed.
"""

from __future__ import annotations

import functools

import pytest
from conftest import FIXTURES

pytest.importorskip("tree_sitter_java")

from bench import java_oracle  # noqa: E402
from ripple import java_jdk  # noqa: E402
from ripple.indexer import build_graph, walk_repo  # noqa: E402
from ripple.java_indexer import Declarations, load_parser, scan_unread  # noqa: E402
from ripple.java_resolver import JavaResolver  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

PROBES = FIXTURES / "java" / "probes" / "ondemand"


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


def name_of(make_repo, imports: str, name: str, extra: dict | None = None, pkg: str = "p"):
    """What the simple name `name` means in `class Use` of package pkg, with these imports (find_simple_type)."""
    head = f"package {pkg};\n" if pkg else ""
    files = {f"{pkg.replace('.', '/')}/Use.java" if pkg else "Use.java": f"{head}{imports}\nclass Use {{}}\n", **(extra or {})}
    declarations, resolver = declared(make_repo, files)
    return resolver.find_simple_type(name, declarations.types[f"{pkg}.Use" if pkg else "Use"], None)


def unknown(name: str) -> tuple:
    return ("ext", "?." + name)


def refused(result: tuple, why: str) -> bool:
    return result[0] == "unres" and why in result[1]


# ---- a JDK type an on-demand import might supply is no JDK type ----------------------------------------------------------------


@pytest.mark.parametrize(
    "imports,name",
    [
        ("import java.util.*;", "List"),
        ("import java.util.*;", "ArrayList"),
        ("import java.io.*;", "Writer"),
        ("import java.util.*;\nimport java.io.*;", "Writer"),
        ("import java.util.*;", "Timer"),
        ("import java.util.*;", "SequencedCollection"),  # only the JDKs from 21 have it
        ("import java.nio.*;", "ByteOrder"),
        ("import java.util.concurrent.*;", "StructuredTaskScope"),
        ("import java.util.*;", "Callable"),  # java.util.concurrent is no part of java.util, and the name may be anyone's
        ("import java.util.*;", "Entry"),  # a member type of Map is no type of java.util
        ("import java.util.*;", "Gadget"),
        ("import java.util.*;\nimport org.x.*;", "List"),
        ("import java.sql.*;", "Timestamp"),
        ("import java.util.Map.*;", "Entry"),
        ("import java.io.*;", "IO"),  # java.io.IO in JDK 23 and 24, java.lang.IO from 25
    ],
)
def test_a_name_a_package_of_the_jdk_might_supply_is_a_type_of_an_unknown_package_and_never_the_jdk_type(make_repo, imports, name):
    # `java.util.List` here would make `class Mine extends List`-like readings use the table's absences (no method `foo`, no member
    # type, final, no constructor): Timer in the file's own package from a jar has a `foo` the table's Timer has not
    assert name_of(make_repo, imports, name) == unknown(name)


def test_a_qualified_name_through_an_on_demand_import_is_unknown_as_well(make_repo):
    declarations, resolver = declared(make_repo, {"p/Use.java": "package p;\nimport java.util.*;\nclass Use {}\n"})
    use = declarations.types["p.Use"]
    assert resolver.resolve_type("Map.Entry<String, String>", use) == ("ext", "?.Map.Entry")
    assert resolver.resolve_type("java.util.Map.Entry", use) == ("ext", "?.java.util.Map.Entry")


def test_every_table_row_is_refused_for_a_name_an_on_demand_import_supplies_and_nothing_resolves_to_a_canonical_name(make_repo):
    # the one gate: no name of the table, in any of its packages, comes back from an on-demand import as the table's type
    # (java.lang's own names are the exception that keeps its old reading, and are not in this list)
    names = [n for n in java_jdk.table().names() if not n.startswith("java.lang.") and n.count(".") < 4][::17]
    imports = "\n".join(f"import {n.rsplit('.', 1)[0]}.*;" for n in names if n.rsplit(".", 1)[0].count(".") >= 1)
    declarations, resolver = declared(make_repo, {"p/Use.java": f"package p;\n{imports}\nclass Use {{}}\n"})
    use = declarations.types["p.Use"]
    for full in names:
        simple = full.rsplit(".", 1)[1]
        if simple in resolver.java_lang:
            continue
        found = resolver.find_simple_type(simple, use, None)
        assert found == unknown(simple), (full, found)


def test_a_name_no_star_supplies_stays_not_visible(make_repo):
    assert name_of(make_repo, "", "Gadget") == ("unres", "type Gadget is not visible")


# ---- java.lang keeps what it always had ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "imports,name,expected",
    [
        ("import java.util.*;", "String", ("ext", "java.lang.String")),
        ("import java.lang.*;", "Thread", ("ext", "java.lang.Thread")),
        ("import org.x.*;", "String", ("ext", "java.lang.String")),
        ("", "ProcessHandle", ("ext", "java.lang.ProcessHandle")),  # nothing else is imported: java.lang is the only source
        ("import java.util.*;", "ProcessHandle", ("ext", "?.ProcessHandle")),  # JDK 9 and later: a library may have it on JDK 8
        ("import java.util.*;", "Record", ("ext", "?.Record")),
    ],
)
def test_a_name_java_lang_supplies_is_read_as_it_was_before_the_on_demand_reading(make_repo, imports, name, expected):
    assert name_of(make_repo, imports, name) == expected


def test_java_lang_names_have_none_of_the_new_refusals_which_is_the_known_assumption(make_repo):
    # the same exposure as any on-demand name (a type of the package that no source shows shadows java.lang) is an assumption of
    # long standing for these names, and it is not widened and not closed here
    lost = {"elsewhere/Object.java": "package p;\npublic class Object {}\n"}
    assert name_of(make_repo, "import java.util.*;", "Object", lost) == ("ext", "java.lang.Object")
    shapes = {"p/Shapes.java": "package p;\npublic class Shapes { public static class Record {} }\n"}
    assert name_of(make_repo, "import static p.Shapes.*;", "Record", shapes) == ("ext", "java.lang.Record")
    assert name_of(make_repo, "import static p.Shapes.Record;", "Record", shapes) == ("ext", "java.lang.Record")


# ---- what the repo's own sources supply is exact -------------------------------------------------------------------------------


def test_a_repo_type_an_on_demand_import_supplies_is_the_name(make_repo):
    extra = {"q/SequencedCollection.java": "package q;\npublic class SequencedCollection {}\n", "q/Other.java": "package q;\npublic class Other {}\n"}
    kind, found = name_of(make_repo, "import java.util.*;\nimport q.*;", "SequencedCollection", extra)
    assert kind == "repo" and found.fqn == "q.SequencedCollection"  # (javac compiles this only for a release before 21)
    # a repo package that lacks the name is no source of it, but is not proof that the JDK's is the name either
    assert name_of(make_repo, "import java.util.*;\nimport q.*;", "List", extra) == unknown("List")


def test_two_repo_types_two_on_demand_imports_supply_are_unresolved(make_repo):
    extra = {f"{q}/Twin.java": f"package {q};\npublic class Twin {{}}\n" for q in ("q", "r")}
    assert name_of(make_repo, "import java.util.*;\nimport q.*;\nimport r.*;", "Twin", extra)[0] == "unres"


def test_a_single_type_import_and_a_type_of_the_package_come_before_the_on_demand_import(make_repo):
    extra = {"q/List.java": "package q;\npublic class List {}\n", "p/Queue.java": "package p;\nclass Queue {}\n"}
    kind, found = name_of(make_repo, "import java.util.*;\nimport q.List;", "List", extra)
    assert (kind, found.fqn) == ("repo", "q.List")
    kind, found = name_of(make_repo, "import java.util.*;", "Queue", extra)
    assert (kind, found.fqn) == ("repo", "p.Queue")  # a type of the package, in ANY file of it


def test_a_type_of_the_default_package_comes_before_the_on_demand_import(make_repo):
    kind, found = name_of(make_repo, "import java.io.*;", "Writer", {"Writer.java": "public class Writer {}\n"}, pkg="")
    assert (kind, found.fqn) == ("repo", "Writer")
    assert name_of(make_repo, "import java.io.*;", "Reader", {"Writer.java": "public class Writer {}\n"}, pkg="") == unknown("Reader")


# ---- what may come before an on-demand import, and ripple cannot show -----------------------------------------------------------


def test_a_single_static_import_may_supply_a_type_and_shadows_the_on_demand_import(make_repo):
    # `import static p.Shapes.Queue;` imports the member TYPE Queue (JLS 6.4.1). A reader that skipped the static imports
    # called `q.add(x)` a call outside the repo for a call javac sends to Shapes.Queue.add.
    result = name_of(make_repo, "import java.util.*;\nimport static p.Shapes.Queue;", "Queue")
    assert refused(result, "single static import of p.Shapes")
    assert name_of(make_repo, "import java.util.*;\nimport static p.Shapes.Queue;", "List") == unknown("List")


def test_a_static_on_demand_import_of_a_repo_type_may_supply_a_member_type(make_repo):
    shapes = {"p/Shapes.java": "package p;\npublic class Shapes { public static class SequencedCollection {} }\n"}
    assert refused(name_of(make_repo, "import static p.Shapes.*;\nimport java.util.*;", "SequencedCollection", shapes), "static member type")
    assert name_of(make_repo, "import static p.Shapes.*;\nimport java.util.*;", "List", shapes) == unknown("List")


def test_a_file_the_reader_could_not_place_may_declare_the_name_in_its_own_package(make_repo):
    # `package p;` in a directory that is not p's: not indexed (its source root is not provable), javac compiles it. Its Writer
    # comes before `import java.io.*` in every file of p, and before a repo package that has a Writer too (momus F4)
    lost = {"elsewhere/Writer.java": "package p;\npublic class Writer {}\n"}
    assert refused(name_of(make_repo, "import java.io.*;", "Writer", lost), "was not read whole")
    assert name_of(make_repo, "import java.io.*;", "Reader", lost) == unknown("Reader")  # only the names it writes
    other = {**lost, "q/Writer.java": "package q;\npublic class Writer {}\n"}
    assert refused(name_of(make_repo, "import q.*;", "Writer", other), "was not read whole")  # not the repo package's Writer


def test_a_package_known_only_from_a_file_the_reader_could_not_place_is_unknown_not_a_repo_package(make_repo):
    # momus F1(c): `package org.x;` in a mismatched directory made `import org.x.*;` a fully known repo package, which no
    # library's package of that name can be. The name stays what it was: a type of a package that is not known
    lost = {"docs/Sample.java": "package org.x;\nclass Sample {}\n"}
    assert name_of(make_repo, "import org.x.*;\nimport java.util.*;", "SequencedCollection", lost) == unknown("SequencedCollection")
    assert refused(name_of(make_repo, "import org.x.*;\nimport java.util.*;", "Sample", lost), "may declare Sample")
    # the package is unknown for java.lang names too: a library's org.x may supply `Record` where the JDK has none (JDK 8 and before 14)
    assert name_of(make_repo, "import org.x.*;", "Record", lost) == unknown("Record")
    assert name_of(make_repo, "import org.x.*;", "String", lost) == ("ext", "java.lang.String")


def index_with_a_failing_file(make_repo, monkeypatch):
    """The declarations of a repo whose p/Gone.java the front end fails on (a RecursionError), and its root."""
    root = make_repo({"p/Use.java": "package p;\nimport java.util.*;\nclass Use {}\n", "p/Gone.java": "package p;\nclass Gone { void gone() {} }\n"})
    real = Declarations._declare_file

    def broken(self, rel, tree, directory):
        if rel.endswith("Gone.java"):
            raise RecursionError
        return real(self, rel, tree, directory)

    monkeypatch.setattr(Declarations, "_declare_file", broken)
    declarations, parser = Declarations(), load_parser()
    for path in sorted(root.rglob("*.java")):
        declarations.add_file(path, root, parser)
    return declarations, root


def test_a_file_the_front_end_failed_on_but_that_reads_as_text_is_unseen_for_what_it_declares(make_repo, monkeypatch):
    declarations, _ = index_with_a_failing_file(make_repo, monkeypatch)
    declarations.finish()
    assert not declarations.unseen_anywhere and not declarations.unseen_unreadable
    assert declarations.unseen_names["p"] == {"Gone"} and dict(declarations.unseen_methods) == {"gone": {(0, False)}}
    resolver = JavaResolver(declarations, JAVA).run()
    use = declarations.types["p.Use"]
    assert refused(resolver.find_simple_type("Gone", use, None), "was not read whole")
    assert resolver.find_simple_type("List", use, None) == unknown("List")


def test_a_file_that_cannot_be_read_at_all_makes_every_guarded_name_unresolved_and_every_method_name_unseen(make_repo, monkeypatch):
    declarations, root = index_with_a_failing_file(make_repo, monkeypatch)
    declarations.note_unseen_file(root / "p" / "Vanished.java")  # a file the walk saw and that went away (OSError)
    declarations.finish()
    assert declarations.unseen_anywhere and declarations.unseen_unreadable
    resolver = JavaResolver(declarations, JAVA).run()
    use = declarations.types["p.Use"]
    assert resolver.find_simple_type("List", use, None)[0] == "unres"
    assert resolver.find_simple_type("String", use, None) == ("ext", "java.lang.String")  # java.lang keeps its reading
    assert resolver.unseen_method("anything", 3)


def test_a_package_line_the_parser_cannot_read_leaves_no_package_known_and_makes_every_guarded_name_unresolved(make_repo):
    # `package;` is an error node: the file is not placed in any package, and it may be in this one (F6: unseen_anywhere)
    result = name_of(make_repo, "import java.util.*;", "List", {"x/Broken.java": "package;\nclass Broken {}\n"})
    assert refused(result, "was not read whole")
    assert name_of(make_repo, "import java.util.*;", "String", {"x/Broken.java": "package;\nclass Broken {}\n"}) == ("ext", "java.lang.String")


def test_an_escaped_identifier_in_a_file_that_was_not_read_whole_is_read_as_the_name_it_spells(make_repo):
    # F6: `public class Timer` is Timer to javac. The text is read as javac reads it (JLS 3.3), so the name is Timer and
    # only Timer: the package is not unseen as a whole
    lost = {"p/Timer.java": "package p;\npublic class \\u0054imer { public void cancelAll() {} }\n"}
    assert refused(name_of(make_repo, "import java.util.*;", "Timer", lost), "was not read whole")
    assert name_of(make_repo, "import java.util.*;", "Anything", lost) == unknown("Anything")
    assert name_of(make_repo, "import java.util.*;", "Timer", {"p/Timer.java": "package p;\nclass Timers {}\n"}) == unknown("Timer")


@pytest.mark.parametrize("path,source", [("p/Timer.java", "package p;\npublic class \\u00zzimer { }\n"), ("p/package-info.java", "package p;\n// see \\u12\nclass Anything { }\n")])
def test_an_escape_that_spells_nothing_in_a_file_that_was_not_read_whole_makes_the_whole_package_unseen(make_repo, path, source):
    # javac refuses such a file, but ripple does not know the build is one that would: any name of the package may be its
    assert refused(name_of(make_repo, "import java.util.*;", "Timer", {path: source}), "was not read whole")


def test_a_backslash_u_in_a_string_or_a_comment_of_such_a_file_is_no_escape_and_costs_nothing(make_repo):
    # a valid `\u0041` in a string or a comment is a character, and `\\u12` is an escaped backslash and a `u` (JLS 3.3)
    source = 'package p;\nclass Quiet { String a = "\\\\u12"; String b = "\\u0041"; /* \\u0041 */ void m( {} }\n'
    assert name_of(make_repo, "import java.util.*;", "Timer", {"p/Quiet.java": source}) == unknown("Timer")


def test_a_module_import_the_grammar_cannot_read_may_supply_any_guarded_name(make_repo):
    # `import module m;` (JDK 23 and later, an import of every package a module exports) is an error node to the parser, and a
    # module of the repo may export repo types
    imports = "import module java.base;\nimport java.util.*;"
    assert refused(name_of(make_repo, imports, "SequencedCollection"), "could not be read")
    assert name_of(make_repo, imports, "String") == ("ext", "java.lang.String")


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_a_type_declared_after_a_line_comment_is_found_whatever_the_line_ending(ending):
    # F5: Java ends a `//` comment at a CR as well (JLS 3.4): `class // generated<CR>Timer` is Timer
    source = f"package p;{ending}public class // generated{ending}Timer {{ }}{ending}"
    assert scan_unread(source).types == {"Timer"}
    assert scan_unread(f"class /* c */{ending}/* d */ Timer {{ }}").types == {"Timer"}


def test_a_class_after_a_carriage_return_only_comment_is_unseen_in_its_package(make_repo):
    lost = {"p/Timer.java": "package p;\rpublic class // generated\rTimer { public void foo() {} }\r"}
    assert refused(name_of(make_repo, "import java.util.*;", "Timer", lost), "was not read whole")


# ---- the .java files of the repo that the walk leaves out are repo code ripple does not read -------------------------------------

SHAPE = """\
    package p;
    import java.util.*;
    class Outer {
        void foo() {}
        class Use extends Timer {
            void go() { foo(); }
        }
    }
    """


def graph(make_repo, files: dict):
    return build_graph(make_repo(files), JAVA)


def edges(g, caller: str) -> set[str]:
    return {e.callee for e in g.edges if e.caller == caller}


def test_a_type_a_package_info_file_declares_is_a_type_of_the_package_ripple_does_not_read(make_repo):
    # javac compiles `class Timer` in package-info.java (momus c5). The walk leaves the file out, and Timer is p's, before the import
    g = graph(make_repo, {"p/Outer.java": SHAPE, "p/package-info.java": "package p;\nclass Timer { void foo() {} }\n"})
    assert edges(g, "p.Outer.Use.go()") == set()  # javac: Timer.foo, inherited
    assert [u for u in g.unresolved if u.caller == "p.Outer.Use.go()"]


def test_a_type_a_generated_source_below_build_output_declares_is_a_type_of_the_package_ripple_does_not_read(make_repo):
    # `target/generated-sources/xjc/p/Timer.java` beside a pom.xml: the walk skips it (momus c1) and javac compiles it
    g = graph(
        make_repo,
        {
            "pom.xml": "<project/>",
            "src/main/java/p/Outer.java": SHAPE,
            "target/generated-sources/xjc/p/Timer.java": "package p;\npublic class Timer { public void foo() {} }\n",
        },
    )
    assert edges(g, "p.Outer.Use.go()") == set()
    assert [u for u in g.unresolved if u.caller == "p.Outer.Use.go()"]


@pytest.mark.parametrize(
    "path",
    ["target/generated-sources/p/Timer.java", "build/generated/p/Timer.java", "out/production/p/Timer.java", "bin/p/Timer.java", ".hidden/p/Timer.java", "p/package-info.java"],
)
def test_every_kind_of_java_file_the_walk_leaves_out_is_noted(make_repo, path):
    markers = {"pom.xml": "<project/>", "build.gradle": "", ".classpath": "", ".idea/x": "", "m.iml": ""}
    body = "package p;\nclass Timer {}\n"
    g = graph(make_repo, {**markers, "src/p/Outer.java": SHAPE, path: body})
    assert edges(g, "p.Outer.Use.go()") == set(), path


def test_a_call_into_a_type_ripple_did_not_read_is_not_counted_outside_the_repo(make_repo):
    # the absence rule ("no repo method has this name") must not read a method of a file that was not read as no method
    files = {
        "pom.xml": "<project/>",
        "src/main/java/p/Outer.java": "package p;\nimport java.util.*;\nclass Outer { void f(Timer t) { t.cancelAll(); } }\n",
        "target/generated-sources/p/Timer.java": "package p;\npublic class Timer { public void cancelAll() {} }\n",
    }
    g = graph(make_repo, files)
    assert g.external_calls == 0 and [u for u in g.unresolved if u.caller == "p.Outer.f(Timer)"]


# ---- the verdicts at the call sites of the probes, pinned: the probes compare each with javac -----------------------------------


def verdicts(probe: str) -> dict[str, tuple[str, str]]:
    """{file:line shown: (kind, info)} for every call site of a probe."""
    found = {}
    for site in java_oracle.read_ripple(PROBES / probe / "src").sites:
        info = site.info if site.kind != "edge" else ",".join(callee for callee, _ in site.info)
        found[f"{site.file}:{site.line} {site.shown}"] = (site.kind, info)
    return found


def judged(probe: str):
    return java_oracle.judge(PROBES / probe / "src", java_oracle.read_javac(PROBES / probe))


def test_probe_types_named_through_on_demand_imports_give_no_edge_through_a_jdk_supertype():
    found = verdicts("jdk-types-named-through-on-demand-imports")
    for site in ("p/Use.java:29 l.record", "p/Use.java:31 l.flush", "p/Use.java:35 n.record", "p/Use.java:36 n.add"):
        kind, why = found[site]
        assert kind == "unres" and "?." in why and "package unknown" in why, (site, found[site])  # Writer, ArrayList: unknown supertypes
    assert not [why for kind, why in found.values() if kind == "edge" and why.startswith(("p.Ledger.", "p.Names."))], found
    assert found["p/Use.java:49 Collections.sort"] == ("ext", "static call on external class (package unknown: on-demand import)")
    assert found["p/Use.java:56 e.getKey"] == ("ext", "instance of external class (package unknown: on-demand import)")
    assert judged("jdk-types-named-through-on-demand-imports").problems == []


def test_probe_java_lang_names_are_what_they_were_before():
    found = verdicts("java-lang-type-of-some-jdks-with-no-other-source")
    assert found["p/Use.java:22 t.note"] == ("edge", "p.Task.note(Item)")  # Thread is in every JDK: java.lang.Thread, as on main
    kind, why = found["p/Use.java:21 b.note"]
    assert kind == "unres" and "?.ProcessHandle" in why  # JDK 9 and later: not decided, as on main
    assert judged("java-lang-type-of-some-jdks-with-no-other-source").problems == []


def test_probe_a_repo_package_supplies_what_the_jdk_of_the_release_lacks():
    found = verdicts("repo-package-and-jdk-package-where-the-jdk-of-the-release-lacks-the-name")
    assert found["p/Use.java:8 c.addFirst"] == ("edge", "q.SequencedCollection.addFirst(Object)")
    assert found["p/Use.java:9 c.size"] == ("edge", "q.SequencedCollection.size()")
    assert judged("repo-package-and-jdk-package-where-the-jdk-of-the-release-lacks-the-name").problems == []


@pytest.mark.parametrize(
    "probe,site,why",
    [
        ("package-of-a-file-the-reader-cannot-place-may-supply-the-name", "p/Use.java:8 c.size", "other.lib that was not read whole"),
        ("package-of-a-file-the-reader-cannot-place-may-supply-the-name", "p/Use.java:9 k.call", "other.lib that was not read whole"),
        ("single-static-import-of-a-member-type-shadows-the-on-demand-import", "p/Use.java:10 q.add", "single static import of p.Shapes"),
        ("static-on-demand-import-of-a-member-type-beside-a-jdk-name", "p/Use.java:14 r.keep", "static member type"),
        ("type-of-the-package-in-a-file-the-reader-cannot-place-beats-the-import", "p/Use.java:7 w.write", "was not read whole"),
    ],
)
def test_probe_a_name_another_source_may_supply_is_unresolved_with_the_reason_and_javac_agrees(probe, site, why):
    kind, info = verdicts(probe)[site]
    assert kind == "unres" and why in info, (kind, info)
    assert judged(probe).problems == []


def test_probe_two_jdk_packages_with_the_name_in_some_jdks_is_a_type_of_an_unknown_package():
    found = verdicts("two-jdk-packages-each-have-the-name-in-some-jdks")
    assert found["p/Use.java:12 IO.println"] == ("ext", "static call on external class (package unknown: on-demand import)")
    assert judged("two-jdk-packages-each-have-the-name-in-some-jdks").problems == []


def test_the_nearer_declarations_probes_are_all_the_repos_and_javac_agrees():
    found = verdicts("nearer-declarations-beat-the-on-demand-import")
    assert found["p/Use.java:16 w.write"] == ("edge", "p.Writer.write(Item)")  # a type of the compilation unit
    assert found["p/Use.java:27 q.add"] == ("edge", "p.B.Queue.add(Item)")  # a member type
    assert found["p/Use.java:43 l.add"] == ("edge", "p.D.f(Item).List.add(Item)")  # a local class
    assert found["p/Use.java:56 m.put"] == ("edge", "p.Base.Map.put(Item)")  # a member type a repo supertype declares
    assert found["p/Use.java:63 d.push"] == ("edge", "q.Deque.push(Object)")  # a single-type import
    assert found["p/Use.java:64 o.get"] == ("edge", "p.Optional.get(Item)")  # a type of the package in another file
    found = verdicts("default-package-type-in-another-file-beats-the-on-demand-import")
    assert found["Use.java:7 w.write"] == ("edge", "Writer.write(Item)")
    assert found["Use.java:11 n.record"] == ("edge", "Names.record(Item)")
    for probe in ("nearer-declarations-beat-the-on-demand-import", "default-package-type-in-another-file-beats-the-on-demand-import"):
        assert judged(probe).problems == []


def test_there_are_probes_for_the_classes_of_doubt():
    assert len([p for p in PROBES.iterdir() if (p / "javac.json").exists()]) >= 10


# ---- the table is not read for these names where the repo patches the JDK, or where it was refused ----------------------------


@pytest.fixture
def refused_table(monkeypatch):
    from ripple import java_resolver

    real = java_jdk.table()
    text = "# rows: 5\n" + "\n".join(real._lines[n] for n in real.names()[:2]) + "\n"
    cut = functools.lru_cache(maxsize=1)(lambda: java_jdk.JdkTable(text))
    java_resolver._all_jdks.cache_clear()
    java_resolver._jdk_layout.cache_clear()
    monkeypatch.setattr(java_jdk, "table", cut)
    yield cut
    monkeypatch.undo()
    java_resolver._all_jdks.cache_clear()
    java_resolver._jdk_layout.cache_clear()


def test_a_refused_table_changes_no_on_demand_name_and_java_lang_reads_as_on_main(make_repo, refused_table):
    assert name_of(make_repo, "import java.util.*;", "List") == unknown("List")
    assert name_of(make_repo, "import java.util.*;", "String") == unknown("String")  # no row says every JDK has it (as on main)
    assert name_of(make_repo, "", "String") == ("ext", "java.lang.String")
