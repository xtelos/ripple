"""Class expressions: ripple does not read their members, and says so wherever it knows a body was skipped.

Each call written inside the body of a class expression (`return class { run() { transform(1); } }`) is recorded as
an unresolved call of the scope around it, with a reason that names the skip, so the same-name hint finds it. None
becomes an edge: the body is still not read. These need tree-sitter and are skipped, with the reason, without it.
"""

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.model import UNREAD_CLASS  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import TYPESCRIPT  # noqa: E402
from ripple.queries import callers, impact  # noqa: E402


def index(make_repo, files):
    return build_graph(make_repo(files), TYPESCRIPT)


CLASS_EXPRESSION_SOURCE = """\
export function transform(x: number) { return x; }
export function make() {
  return class {
    run() { return transform(1); }
    other() { return this.transform(2); }
  };
}
export const wrapper = <T>(base: T) => class { go() { return new Helper(); } };
class Helper {}
"""


def test_calls_inside_a_class_expression_are_flagged_unresolved_and_never_edges(make_repo):
    graph = index(make_repo, {"src/m.ts": CLASS_EXPRESSION_SOURCE})
    assert not [e for e in graph.edges if e.callee == "src/m:transform"]  # not read: no edge
    flagged = {(u.caller, u.text, u.line) for u in graph.unresolved if u.reason == UNREAD_CLASS}
    assert ("src/m:make", "transform", 4) in flagged
    assert ("src/m:make", "this.transform", 5) in flagged
    assert ("src/m:wrapper", "Helper", 8) in flagged  # `new Helper()` is a call too


def test_impact_and_callers_list_the_skipped_class_expression_calls_with_the_reason(make_repo):
    graph = index(make_repo, {"src/m.ts": CLASS_EXPRESSION_SOURCE})
    for result in (impact(graph, "src/m:transform"), callers(graph, "src/m:transform")):
        found = {(m["call"], m["location"]) for m in result["possible_missed_callers"]}
        assert {("transform", "src/m.ts:4"), ("this.transform", "src/m.ts:5")} <= found
        assert all(m["reason"] == UNREAD_CLASS for m in result["possible_missed_callers"] if m["caller"] == "src/m:make")


def test_a_symbol_no_class_expression_calls_gets_no_new_key(make_repo):
    graph = index(make_repo, {"src/m.ts": CLASS_EXPRESSION_SOURCE})
    assert "possible_missed_callers" not in callers(graph, "src/m:make")


def test_a_long_callee_keeps_the_name_it_ends_with_and_super_and_import_are_not_flagged(make_repo):
    long_chain = "this.registry" + ".withAVeryLongNameThatGoesOnAndOn" * 3 + ".get().transform(3)"
    source = f"""
    export function make() {{
      return class extends Object {{
        constructor() {{ super(); import("./x"); {long_chain}; }}
      }};
    }}
    """
    graph = index(make_repo, {"src/m.ts": source})
    flagged = [u.text for u in graph.unresolved if u.reason == UNREAD_CLASS]
    assert len(flagged) == 2 and any(text.endswith(".transform") for text in flagged)  # `.get()` is the other one
    assert all(len(text) <= 100 for text in flagged)
    assert "super" not in flagged and not any(text.startswith("import") for text in flagged)


def test_a_decorator_on_a_nameless_default_class_is_still_a_decoration(make_repo):
    graph = index(make_repo, {"src/m.ts": "export function tag(t: any) { return t; }\nexport default @tag class {\n  run() { return 1; }\n}\n"})
    assert [(d.holder, d.line, d.target) for d in graph.decorations] == [("src/m", 2, "src/m:tag")]


def test_a_default_export_with_no_name_is_flagged_the_same_way(make_repo):
    graph = index(make_repo, {"src/m.ts": "export function transform(x: number) { return x; }\nexport default class {\n  run() { return transform(1); }\n}\n"})
    assert [(u.caller, u.text, u.line) for u in graph.unresolved if u.reason == UNREAD_CLASS] == [("src/m", "transform", 3)]
    assert not graph.edges


def test_a_class_nobody_skips_is_still_read_and_flags_nothing(make_repo):
    graph = index(make_repo, {"src/m.ts": "export function transform(x: number) { return x; }\nexport class K { run() { return transform(1); } }\n"})
    assert {(e.caller, e.callee) for e in graph.edges} == {("src/m:K.run", "src/m:transform")}
    assert not [u for u in graph.unresolved if u.reason == UNREAD_CLASS]


def test_the_text_output_names_the_skip(make_repo, monkeypatch, capsys, tmp_path):
    from ripple.cli import main

    repo = make_repo({"src/m.ts": CLASS_EXPRESSION_SOURCE})
    monkeypatch.setenv("RIPPLE_CACHE_DIR", str(tmp_path / "cache"))
    assert main(["callers", "src/m:transform", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "calls with the same name inside classes ripple does not read (2), check by hand:" in out
    assert "  this.transform  in src/m:make  src/m.ts:5  (inside a class ripple does not read)" in out
    assert main(["impact", "src/m:transform", "--repo", str(repo)]) == 0
    assert "(inside a class ripple does not read)" in capsys.readouterr().out


# ---- the pinned tsyringe checkout (task 24)


def test_transform_in_tsyringe_lists_the_calls_a_class_expression_hides():
    from eval.repos import load_repos

    checkout = load_repos()["tsyringe"].checkout
    if not checkout.is_dir():
        pytest.skip("the pinned tsyringe checkout is not fetched (python -m eval.fetch tsyringe)")
    graph = build_graph(checkout, TYPESCRIPT)
    # the class expression `autoInjectable` returns calls `.transform(...)` three times and is not read
    hidden = [u for u in graph.unresolved if u.reason == UNREAD_CLASS and u.file == "src/decorators/auto-injectable.ts"]
    assert len(hidden) == 20 and len([u for u in hidden if u.text.endswith(".transform")]) == 3
    one = "src/__tests__/transform.test:Injecting with transform should work.BarTransform.transform"
    for result in (impact(graph, one), callers(graph, one)):
        listed = [m for m in result["possible_missed_callers"] if m.get("reason") == UNREAD_CLASS]
        assert [m["location"] for m in listed] == ["src/decorators/auto-injectable.ts:31", "src/decorators/auto-injectable.ts:37", "src/decorators/auto-injectable.ts:49"]
