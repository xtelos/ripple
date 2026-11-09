"""TypeScript decorators and class expressions: what the front end records, and what each question says.

A decoration is not a call edge (ripple does not model what a decorator does to what it decorates), but it is not
nothing either: the places a symbol is written as `@sym` or `@sym(...)` are facts, and every question about the symbol
lists them apart, flagged. Calls inside a class expression are not read, and each one is recorded as an unresolved
call, so the same-name hint finds it. These need tree-sitter and are skipped, with the reason, without it.
"""

import json
import sys

import pytest
from conftest import FIXTURES  # noqa: F401  (keeps the conftest path set up)
from stdio_client import StdioClient

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cli import main  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import TYPESCRIPT  # noqa: E402
from ripple.queries import callees, callers, impact, path  # noqa: E402

DEC = """\
export function tag(target: any) { return target; }
export function route(prefix: string) { return function (t: any, k?: any, d?: any) {}; }
export function param(t: any, k: any, i: number) {}
export function prop(t: any, k: any) {}
export function unused() {}
export function never() { return 1; }
"""

APP = """\
import { tag, route, param, prop } from "./dec";
import * as d from "./dec";

@tag
export class A {}

@route("/b")
class B {
  @prop name = "";
  @route("/m")
  handle(@param x: string, @param y: string) { return x; }
}

@d.route("/n")
class C {}

export function build() {
  @tag
  class Local {}
  return Local;
}

export function run() { return route("/direct"); }
"""


def index(make_repo, files):
    return build_graph(make_repo(files), TYPESCRIPT)


def at(source, needle, nth=1):
    """The 1-based line of the nth line containing needle."""
    return [i for i, line in enumerate(source.splitlines(), 1) if needle in line][nth - 1]


@pytest.fixture
def project(make_repo):
    repo = make_repo({"src/dec.ts": DEC, "src/app.ts": APP, "tsconfig.json": "{}"})
    return repo, build_graph(repo, TYPESCRIPT)


def sites(entries):
    return sorted(e["applied_at"] for e in entries)


def test_every_way_of_writing_a_decorator_is_recorded_with_what_it_resolved_to(project):
    _, graph = project
    found = sorted((d.holder, d.line, d.text, d.target) for d in graph.decorations)
    param_line = at(APP, "@param x")
    assert found == sorted(
        [
            ("src/app", at(APP, "@tag"), "@tag", "src/dec:tag"),  # on a class
            ("src/app", at(APP, '@route("/b")'), '@route("/b")', "src/dec:route"),  # a factory
            ("src/app", at(APP, "@prop"), "@prop", "src/dec:prop"),  # on a property
            ("src/app", at(APP, '@route("/m")'), '@route("/m")', "src/dec:route"),  # on a method
            ("src/app", param_line, "@param", "src/dec:param"),  # on a parameter
            ("src/app", param_line, "@param", "src/dec:param"),  # the second parameter, same line
            ("src/app", at(APP, "@d.route"), '@d.route("/n")', "src/dec:route"),  # through a namespace import
            ("src/app:build", at(APP, "@tag", 2), "@tag", "src/dec:tag"),  # a class inside a function
        ]
    )


def test_a_decoration_is_never_recorded_as_a_call_edge(project):
    _, graph = project
    assert {(e.caller, e.callee) for e in graph.edges} == {("src/app:run", "src/dec:route")}


def test_callers_of_a_symbol_used_only_as_a_decorator_is_empty_but_not_silent(project):
    _, graph = project
    result = callers(graph, "src/dec:tag")
    assert result["count"] == 0
    assert sites(result["decorations"]) == sorted([f"src/app.ts:{at(APP, '@tag')}", f"src/app.ts:{at(APP, '@tag', 2)}"])
    assert {e["in"] for e in result["decorations"]} == {"src/app", "src/app:build"}
    assert "not modelled" in result["decoration_note"]


def test_a_decorator_also_called_directly_keeps_that_call_exact(project):
    _, graph = project
    result = callers(graph, "src/dec:route")
    direct_call = 'route("/direct")'
    direct_site = f"src/app.ts:{at(APP, direct_call)}"
    assert [(c["symbol"], c["call_sites"]) for c in result["callers"]] == [("src/app:run", [direct_site])]
    assert len(result["decorations"]) == 3  # /b, /m and d.route; the direct call is a call, not a decoration
    assert direct_site not in sites(result["decorations"])


def test_impact_and_path_and_callees_say_it_too(project):
    _, graph = project
    result = impact(graph, "src/dec:param")
    assert result["affected"] == 0
    assert len(result["decorations"]) == 2
    assert path(graph, "src/dec:unused", "src/dec:prop")["decorations"][0]["written"] == "@prop"
    applied = callees(graph, "src/app")["applies_decorators"]
    assert {a["decorator"] for a in applied} == {"src/dec:tag", "src/dec:route", "src/dec:prop", "src/dec:param"}
    assert callees(graph, "src/app")["callees"] == []


def test_a_symbol_with_no_decorator_use_answers_as_before(project):
    _, graph = project
    for result in (callers(graph, "src/dec:unused"), impact(graph, "src/dec:never"), callees(graph, "src/dec:unused")):
        assert not {"decorations", "possible_decorations", "decoration_note", "applies_decorators"} & set(result)


def test_the_unresolved_decorator_calls_are_still_there(project):
    _, graph = project
    reasons = {u.reason for u in graph.unresolved if u.text.startswith("@")}
    assert len(reasons) == 1 and "ripple does not follow what it does" in reasons.pop()


def test_calls_in_a_decorators_arguments_belong_to_the_scope_that_holds_the_class(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            export function delay(f: () => any) { return f; }
            export function inject(x: any) { return function (t: any, k: any, i: number) {}; }
            export function field(x: any) { return function (t: any, k: any) {}; }
            export class K {
              @field(delay(() => 1)) value = 1;
              constructor(@inject(delay(() => 2)) a: number) {}
              go(@inject(delay(() => 3)) b: number) {}
            }
            """
        },
    )
    callers_of_delay = sorted((e.caller, e.line) for e in graph.edges if e.callee == "src/m:delay")
    assert callers_of_delay == [("src/m", 6), ("src/m", 7), ("src/m", 8)]  # never `src/m:K`, `src/m:K.constructor`


def test_a_decorator_named_by_a_block_local_declaration_keeps_its_plain_name(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            export function tag(t: any) { return t; }
            export function build() {
              { function tag(t: any) { return t; } @tag class X {} }
              { class tag {} @tag class Y {} }
            }
            """
        },
    )
    assert graph.decorations and {d.name for d in graph.decorations} == {"tag"}


def test_a_decorator_that_comes_from_outside_the_repo_is_not_taken_for_a_repo_symbol(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            import { tag } from "some-package";
            export function tag2() {}
            @tag
            export class A {}
            """,
            "src/own.ts": "export function tag() {}\n",
        },
    )
    assert graph.decorations == []  # `tag` is proven to be the package's, so it is not `src/own:tag`
    assert "decorations" not in callers(graph, "src/own:tag")


def test_a_decorator_name_that_does_not_resolve_is_a_possible_site_for_a_symbol_of_that_name(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.ts": """
            import { api } from "./gone";
            @api.route()
            export class A {}
            """,
            "src/own.ts": "export function route() {}\n",
        },
    )
    result = callers(graph, "src/own:route")
    assert "decorations" not in result
    assert [d["written"] for d in result["possible_decorations"]] == ["@api.route()"]


def test_json_and_text_output_of_the_cli(project, monkeypatch, capsys, tmp_path):
    repo, _ = project
    monkeypatch.setenv("RIPPLE_CACHE_DIR", str(tmp_path / "cache"))
    assert main(["callers", "src/dec:tag", "--repo", str(repo), "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["callers"] == [] and len(shown["decorations"]) == 2
    assert main(["impact", "src/dec:tag", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "0 callers" in out
    assert "applied as a decorator (2), not calls; what a decorator does is not modelled:" in out
    assert f"src/app.ts:{at(APP, '@tag')}" in out


def test_the_mcp_tools_return_the_same_flagged_lists(project, tmp_path):
    repo, _ = project
    client = StdioClient(
        [sys.executable, "-m", "ripple", "serve", "--repo", str(repo)], env={"RIPPLE_CACHE_DIR": str(tmp_path / "cache")}
    )
    client.initialize()
    try:
        for tool in ("callers", "impact"):
            reply = client.request("tools/call", {"name": tool, "arguments": {"symbol": "src/dec:tag"}})
            payload = json.loads(reply["result"]["content"][0]["text"])
            assert len(payload["decorations"]) == 2 and "not modelled" in payload["decoration_note"]
        tools = {t["name"]: t for t in client.request("tools/list")["result"]["tools"]}
        assert "decorator" in tools["callers"]["description"] and "decorator" in tools["impact"]["description"]
    finally:
        assert client.close() == 0


# ---- the pinned tsyringe checkout (tasks 24 and 25)


def tsyringe():
    from eval.repos import load_repos

    checkout = load_repos()["tsyringe"].checkout
    if not checkout.is_dir():
        pytest.skip("the pinned tsyringe checkout is not fetched (python -m eval.fetch tsyringe)")
    return checkout


@pytest.fixture(scope="module")
def tsyringe_graph():
    return build_graph(tsyringe(), TYPESCRIPT)


def test_registry_in_tsyringe_lists_every_place_it_is_applied(tsyringe_graph):
    result = callers(tsyringe_graph, "src/decorators/registry:registry")
    assert result["count"] == 0  # it is never called by name
    assert len(result["decorations"]) == 12
    files = sorted({e["applied_at"].rsplit(":", 1)[0] for e in result["decorations"]})
    assert files == [
        "src/__tests__/fixtures/03-test-case-A03-lazy-injects-B03-interface.ts",
        "src/__tests__/fixtures/03-test-case-B03-lazy-injects-A03-interface.ts",
        "src/__tests__/global-container.test.ts",
    ]
    assert len(impact(tsyringe_graph, "src/decorators/registry:registry")["decorations"]) == 12
