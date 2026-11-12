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


def test_every_probe_has_javacs_answer_and_there_are_enough_of_them():
    assert len(DIRECTORIES) >= 150
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
