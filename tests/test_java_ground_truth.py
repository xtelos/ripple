"""bench/ground_truth_java.json against what ripple makes of bench/fixtures/java/shop-java.

The truth is written from what Java does, site by site (see its `definition`): each rule of the Java resolver is
there in both directions, an edge where the source proves it and an unresolved call, with its reason, where it does
not. This needs tree-sitter-java and no JDK; tests/test_java_javac.py checks the same fixture against javac.
"""

import json
from collections import defaultdict
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_java")

from conftest import FIXTURES  # noqa: E402

from ripple.cache import load_repo  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

BENCH = Path(__file__).resolve().parent.parent / "bench"
TRUTH = json.loads((BENCH / "ground_truth_java.json").read_text())
FIXTURE = FIXTURES / "java" / "shop-java"


@pytest.fixture(scope="module")
def graph():
    return build_graph(FIXTURE, JAVA)


def _source_line(file: str, line: int) -> str:
    return (FIXTURE / file).read_text().splitlines()[line - 1]


def test_the_truth_is_well_formed_and_names_lines_the_fixture_has():
    ids = [s["id"] for s in TRUTH["sites"]] + [n["id"] for n in TRUTH["never"]]
    assert len(ids) == len(set(ids)) and len(TRUTH["sites"]) > 50
    for s in TRUTH["sites"]:
        assert _source_line(s["file"], s["line"]).find(s["text"]) >= 0, f"{s['id']}: {s['file']}:{s['line']} does not hold {s['text']!r}"
        assert s["why"] and s["caller"] and s["call"] and isinstance(s["edges"], list), s["id"]
        assert not (s["edges"] and s.get("unresolved")), f"{s['id']} is proven or unresolved, never both"
    groups = {s["group"] for s in TRUTH["sites"]}
    assert groups == {"overloads", "strict", "names", "dispatch", "constructors", "typing"}


def _edges_at(graph, site) -> set[str]:
    """`kind callee` of every edge the caller writes at the site's line whose callee is named `call`."""
    return {
        f"{e.kind} {e.callee}"
        for e in graph.edges
        if e.caller == site["caller"] and e.file == site["file"] and e.line == site["line"] and graph.symbols[e.callee].name == site["call"]
    }


def _reasons_at(graph, site) -> list[str]:
    """The reasons of the unresolved calls the caller has at the site's line."""
    return [u.reason for u in graph.unresolved if u.caller == site["caller"] and u.file == site["file"] and u.line == site["line"]]


@pytest.mark.parametrize("site", TRUTH["sites"], ids=lambda s: s["id"])
def test_each_site_is_what_java_does(graph, site):
    assert _edges_at(graph, site) == set(site["edges"]), site["why"]
    if site.get("unresolved"):
        reasons = _reasons_at(graph, site)
        assert any(site["unresolved"] in reason for reason in reasons), f"{site['id']}: {reasons}"


@pytest.mark.parametrize("never", TRUTH["never"], ids=lambda n: n["id"])
def test_no_edge_that_the_rules_were_written_to_avoid(graph, never):
    file = graph.symbols[never["caller"]].file
    wrong = [e for e in graph.edges if e.caller == never["caller"] and e.callee == never["callee"] and e.file == file and e.line == never["line"]]
    assert wrong == [], never["why"]


def test_the_layout_is_read_as_the_truth_says(graph):
    layout = TRUTH["layout"]
    for file, reason in layout["not_indexed"].items():
        assert [e for e in graph.parse_errors if e.startswith(file) and reason in e], graph.parse_errors
        assert file not in graph.modules
    for file in layout["not_read"]:
        assert file not in graph.modules and not any(s.file == file for s in graph.symbols.values())
    shared = defaultdict(list)
    for file, module in graph.modules.items():
        shared[module].append(file)
    for module, files in layout["shared_module_names"].items():
        assert sorted(shared[module]) == sorted(files)
        assert module not in graph.symbols  # neither file's Flavor is a symbol: they are not mixed
    for reason, count in layout["external"].items():
        assert graph.external_reasons.get(reason) == count, reason


def test_the_fixture_is_read_by_the_registry_from_its_files_alone():
    # no --lang, no pom.xml read: the files say Java, and what the walk skipped says so
    loaded = load_repo(FIXTURE)
    assert [language.name for language in loaded.languages] == ["java"]
    assert loaded.graph.stats()["files"] == len(loaded.graph.modules) > 15
