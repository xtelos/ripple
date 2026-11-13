"""Many small Java programs, each with javac's answer beside it: ripple may be silent, but it may not be wrong.

bench/fixtures/java/probes/<area>/<probe>/src is a legal Java program and javac.json beside it holds what javac made
of every call in it (the invoke instructions, with their source lines, their order and the code that holds them, and
each class's supertypes, declared methods, access flags and bridges; bench/java_probes.py writes it, by hand, with a JDK).
The areas follow the Java Language Specification: accessibility, overload resolution, shadowing, nested classes, enums
and records, imports, qualified this and super, interfaces, generics, members inherited from the JDK, arrays,
initialisers and raw types, comments, patterns and type variables, plus the programs a review of the resolver used to
break it. For every probe, with no JDK needed here, bench/java_oracle.py pairs each call site ripple saw with the invoke
javac wrote for it (by name, argument count and order, not by line alone) and judges ripple's answer there:

  * every call and constructor edge is the method javac invoked,
  * every override edge is a method that a subtype of the static receiver type really runs for the call: declared with
    the invoked descriptor (or the one a javac bridge calls) or inherited from a superclass, not hidden by a closer
    declaration, and in the same package when the invoked method is package-private, and
  * no call ripple counts as outside the repo may run code of the probe: javac's target, or a method that a subtype
    of the receiver declares or inherits, is not declared by any class of the probe.

How many calls ripple proves is not asserted: an unresolved call is allowed, a wrong edge or a wrong "external" is not.
"""

import json
from collections import Counter

import pytest
from conftest import FIXTURES

pytest.importorskip("tree_sitter_java")

from bench import java_oracle  # noqa: E402

PROBES = FIXTURES / "java" / "probes"
DIRECTORIES = sorted(p.parent for p in PROBES.rglob("src") if p.is_dir())


def judge(directory):
    return java_oracle.judge(directory / "src", java_oracle.read_javac(directory))


@pytest.mark.parametrize("directory", DIRECTORIES, ids=[str(d.relative_to(PROBES)) for d in DIRECTORIES])
def test_ripple_is_never_wrong_where_javac_decides(directory):
    oracle = judge(directory)
    assert oracle.problems == []
    assert oracle.reassigned == [], "a probe must pair its calls with javac's invokes without a second guess"


VERSIONED = [(d, name) for d in DIRECTORIES for name in java_oracle.other_answers(d)]


@pytest.mark.parametrize("directory,name", VERSIONED, ids=[f"{d.relative_to(PROBES)}:{n}" for d, n in VERSIONED])
def test_ripple_is_never_wrong_for_the_other_javac_a_probe_records(directory, name):
    """A probe whose meaning depends on the JDK (`javac-8.json` beside `javac.json`, which is JDK 21's): the answer is judged
    against each, so a call that one JDK sends to the repo and another to the JDK is never an edge nor an external."""
    oracle = java_oracle.judge(directory / "src", java_oracle.read_javac(directory, name))
    assert oracle.problems == []
    assert oracle.reassigned == []


VERSION_ONLY = {"version-only-thread-destroy": "destroy", "version-only-arraylist-getfirst": "getFirst", "version-only-enum-describeconstable": "describeConstable"}


@pytest.mark.parametrize("probe,method", sorted(VERSION_ONLY.items()))
def test_a_member_only_some_jdks_have_is_decided_by_neither_answer(probe, method):
    directory = PROBES / "jdk" / probe
    targets = {}
    for name in ("javac.json", *java_oracle.other_answers(directory)):
        rows = [r for r in java_oracle.read_javac(directory, name).by_file["p/Outer.java"] if r[4] == method]
        assert len(rows) == 1, (name, rows)
        targets[name] = (rows[0][2], rows[0][3])
    assert len(set(targets.values())) == len(targets) == 2, f"javac 8 and javac 21 must send the call to different methods: {targets}"
    sites = [s for s in java_oracle.read_ripple(directory / "src").sites if s.name == method]
    assert len(sites) == 1 and sites[0].kind == "unres", [(s.kind, s.info) for s in sites]


def test_a_member_only_the_newest_jdks_have_is_decided_by_none_of_the_answers_process_close_and_comparator_max():
    # JDK 26 gave Process a supertype (Closeable) and close(), and Comparator the default methods max and min: javac 21 and 25 call
    # Outer's close() and max(a, b), javac 26 calls Process.close() and Comparator.max. A table that stopped at JDK 25 sent both
    # to Outer (an edge that is wrong on 26), and called `c.close()` on an AutoCloseable (Use.java, which compiles only on 26)
    # a call outside the repo, though it runs Mine.close. All three calls stay unresolved.
    directory = PROBES / "jdk" / "version-only-process-close"
    assert java_oracle.other_answers(directory) == ["javac-25.json", "javac-26.json"]
    targets = {}
    for name in ("javac.json", "javac-25.json", "javac-26.json"):
        answer = java_oracle.read_javac(directory, name)
        rows = [r for file_rows in answer.by_file.values() for r in file_rows if r[4] in ("close", "max")]
        targets[name] = sorted((r[0], r[1], r[2], r[3]) for r in rows)
    assert targets["javac.json"] == targets["javac-25.json"] == [
        ("p/Outer.java", 16, "invokestatic", "p.Outer"),  # max(a, b)
        ("p/Outer.java", 20, "invokestatic", "p.Outer"),  # close()
    ]
    assert targets["javac-26.json"] == [
        ("p/Outer.java", 16, "invokevirtual", "p.Outer$ByLength"),
        ("p/Outer.java", 20, "invokevirtual", "p.Outer$Proc"),
        ("p/Use.java", 11, "invokeinterface", "java.lang.AutoCloseable"),
    ]
    sites = [s for s in java_oracle.read_ripple(directory / "src").sites if s.name in ("close", "max")]
    assert sorted((s.file, s.name, s.kind) for s in sites) == [("p/Outer.java", "close", "unres"), ("p/Outer.java", "max", "unres"), ("p/Use.java", "close", "unres")]


def test_a_source_file_that_needs_a_newer_javac_is_left_out_of_an_older_ones_answer(tmp_path):
    from bench import java_probes

    src = tmp_path / "src" / "p"
    src.mkdir(parents=True)
    (src / "Old.java").write_text("package p;\npublic class Old {}\n")
    (src / "New.java").write_text("// javac-min: 26\npackage p;\npublic class New {}\n")
    (src / "Odd.java").write_text("package p;\n// javac-min: 99 (not on the first line: no marker)\npublic class Odd {}\n")
    assert [p.name for p in java_probes.sources_for(tmp_path, 25)] == ["Odd.java", "Old.java"]
    assert [p.name for p in java_probes.sources_for(tmp_path, 26)] == ["New.java", "Odd.java", "Old.java"]
    assert [p.name for p in java_probes.sources_for(tmp_path, 8)] == ["Odd.java", "Old.java"]


def test_every_probe_has_javacs_answer_and_there_are_enough_of_them():
    assert len(DIRECTORIES) >= 185
    missing = [str(d.relative_to(PROBES)) for d in DIRECTORIES if not (d / "javac.json").exists()]
    assert missing == [], f"run bench/java_probes.py (needs a JDK) for {missing}"
    for d in DIRECTORIES:
        files = {p.relative_to(d / "src").as_posix() for p in (d / "src").rglob("*.java")}
        data = json.loads((d / "javac.json").read_text())
        assert {row[0] for row in data["invokes"]} <= files, d
        assert all(len(row) == 8 for row in data["invokes"]) and all("bridges" in c for c in data["classes"].values()), d


def test_the_probes_are_not_a_vacuous_pass():
    total = Counter()
    for d in DIRECTORIES:
        total += judge(d).tally
    assert total["call"] >= 250 and total["constructor"] >= 70 and total["override"] >= 40 and total["external"] >= 90, total
    assert not [k for k in total if k.startswith("WRONG")], total


def test_javap_writes_a_member_class_of_a_generic_class_with_dots_and_the_oracle_reads_it_as_the_binary_name():
    """`new Inner<K>() { ... }` inside `class Outer<K, V>`: javap's header says `extends p.Outer.Inner`, the class is `p.Outer$Inner`.
    Without the mapping an inherited method looks declared by no class above the anonymous one (gson's LinkedTreeMap)."""
    empty = {"methods": [], "bridges": [], "compiler": []}
    data = {
        "invokes": [],
        "classes": {
            "p.Outer$Inner": {"supers": ["java.lang.Object"], **empty},
            "p.Outer$1": {"supers": ["p.Outer.Inner", "java.lang.Object", "java.util.Map.Entry"], **empty},
        },
    }
    javac = java_oracle.Javac(data)
    assert javac.supers("p.Outer$1") == {"p.Outer$Inner", "java.lang.Object", "java.util.Map.Entry"}  # a name of the JDK is left alone
    assert "p.Outer$1" in javac.below("p.Outer$Inner")
