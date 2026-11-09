"""`impact` says where the decorators it finds are applied, because the reach continues there.

`helper` is called by the decorator function `track`, and `track` is written as `@track()` on two classes: changing
`helper` changes what runs when those classes are defined. The decorated classes are not callers (what a decorator does
is not followed), but the sites are not nothing either, so `impact` lists them under `decorations_of_callers`, each
naming the affected symbol it applies. The edge set, the `affected` count and `more_beyond_depth` are unchanged.
These need tree-sitter and are skipped, with the reason, without it.
"""

import pytest
from conftest import FIXTURES  # noqa: F401  (keeps the conftest path set up)

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cli import main  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import TYPESCRIPT  # noqa: E402
from ripple.queries import impact  # noqa: E402

FILES = {
    "src/lib.ts": """
    export function helper(): number { return 1; }
    export function track(..._a: unknown[]) { helper(); return (t: unknown) => t; }
    export function viaTrack() { track(); }
    export class Deco { constructor() { helper(); } }
    export function lone() { helper(); }
    """,
    "src/use.ts": """
    import { track, Deco } from "./lib";
    @track()
    export class A {}
    @track()
    export class B {}
    @Deco
    export class C {}
    """,
    "tsconfig.json": "{}",
}


@pytest.fixture
def graph(make_repo):
    return build_graph(make_repo(FILES), TYPESCRIPT)


def test_the_sites_where_an_affected_decorator_is_applied_are_listed_and_name_the_affected_symbol(graph):
    result = impact(graph, "src/lib:helper")
    listed = {(d["applied_at"], d["decorator"], d["distance"]) for d in result["decorations_of_callers"]}
    assert ("src/use.ts:3", "src/lib:track", 1) in listed
    assert ("src/use.ts:5", "src/lib:track", 1) in listed
    assert "not modelled" in result["decorations_of_callers_note"]


def test_a_decorator_class_is_found_through_its_constructor(graph):
    result = impact(graph, "src/lib:helper")
    assert ("src/use.ts:7", "src/lib:Deco.constructor") in {(d["applied_at"], d["decorator"]) for d in result["decorations_of_callers"]}


def test_nothing_else_changes_the_decorated_classes_are_not_callers(graph):
    result = impact(graph, "src/lib:helper")
    affected = {e["symbol"] for entries in result["by_file"].values() for e in entries}
    assert affected == {"src/lib:track", "src/lib:viaTrack", "src/lib:Deco.constructor", "src/lib:lone"}
    assert result["more_beyond_depth"] is False
    assert "decorations" not in result  # helper itself is never written as a decorator
    assert [e for e in graph.edges if e.file == "src/use.ts"] == []


def test_a_symbol_whose_callers_are_not_decorators_has_no_such_key(graph):
    assert "decorations_of_callers" not in impact(graph, "src/lib:track")  # track is a decorator, not a caller of one
    assert "decorations_of_callers" not in impact(graph, "src/lib:lone")


def test_the_list_is_bounded_and_the_count_left_out_is_reported(graph):
    result = impact(graph, "src/lib:helper", limit=1)
    assert len(result["decorations_of_callers"]) == 1
    assert result["decorations_of_callers_omitted"] == 2
    assert result["truncated"] is True  # a site left out is found and not listed


def test_kinds_without_calls_lists_none(graph):
    assert "decorations_of_callers" not in impact(graph, "src/lib:helper", kinds=["renders"])


def test_the_command_line_prints_them(make_repo, capsys, monkeypatch, tmp_path):
    repo = make_repo(FILES)
    monkeypatch.setenv("RIPPLE_CACHE_DIR", str(tmp_path / "cache"))
    assert main(["impact", "src/lib:helper", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "src/use.ts:3" in out and "src/lib:track" in out
