"""A JavaScript repo with a hand-written truth: every edge ripple draws in bench/fixtures/js/inventory-js, and every call it
leaves unresolved, is written here from reading the fixture's source (not copied from ripple's output).

These need tree-sitter and are skipped, with the reason, without it.
"""

import pytest
from conftest import FIXTURES

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cache import load_repo  # noqa: E402
from ripple.cli import main  # noqa: E402
from ripple.queries import callers, find_symbol  # noqa: E402

REPO = FIXTURES / "js" / "inventory-js"

# (kind, caller, callee). Each is a call or JSX element the source writes, to a function the source declares.
EDGES = {
    # ./format is format.js, ./badge is badge.jsx
    ("call", "lib/app:create", "lib/app:helper"),
    ("call", "lib/app:create", "lib/format:format"),
    ("call", "lib/app:render", "lib/badge:Badge"),
    ("call", "lib/main:main", "lib/app:render"),  # a default export, imported under the name it was given there
    ("call", "lib/aliased:viaAlias", "lib/format:format"),  # `@lib/format` through the jsconfig's paths
    # JSX in a .jsx file (a call inside the element) and in a .js file (the element itself)
    ("call", "lib/badge:Badge", "lib/format:format"),
    ("renders", "lib/panel:Panel", "lib/badge:Badge"),
    # a class: #private members, a static block, a getter, an async method, a generator, `new`
    ("call", "lib/counter:Counter.#bump", "lib/format:format"),
    ("call", "lib/counter:Counter.increment", "lib/counter:Counter.#bump"),
    ("call", "lib/counter:Counter.label", "lib/counter:Counter.#bump"),
    ("call", "lib/counter:Counter.load", "lib/counter:Counter.increment"),
    ("call", "lib/counter:Counter.items", "lib/counter:Counter.increment"),
    ("constructor", "lib/counter:Counter.make", "lib/counter:Counter"),
    ("call", "lib/counter:build", "lib/counter:Counter.make"),
    ("constructor", "lib/counter:read", "lib/counter:Counter"),
    ("call", "lib/counter:read", "lib/counter:Counter.label"),  # reading the getter of a `new Counter()` runs it
    # `helper?.()` of a function declared in the repo is a call of it
    ("call", "lib/optional:viaOptional", "lib/app:helper"),
    # export *, export { f as g } from, a default export, and a pair of files that are one source
    ("call", "esm/c:useAll", "esm/b:fallback"),
    ("call", "esm/c:useAll", "esm/a:other"),
    ("call", "esm/c:useAll", "esm/a:f"),  # g is f, re-exported under another name
    ("call", "esm/c:useAll", "esm/dual:dual"),
    ("call", "esm/c:useAll", "esm/dual.mjs:dual"),
    # `from "../"` in src/sub: the directory above, whose index.js is src
    ("call", "src:entry", "src/sub/file:deep"),
    ("call", "src/sub/file:again", "src:entry"),
}

# What the source calls and ripple must not say it knows: caller -> (the call, a part of the reason).
UNRESOLVED = {
    ("lib/counter:Counter.load", "source"): "unannotated parameter",
    ("lib/optional:viaOptional", "box.method"): "unannotated parameter",  # box?.method()
    ("lib/optional:viaOptional", "maybe"): "unannotated parameter",  # maybe?.()
    # (helper < a > (b)) is two comparisons in JavaScript: no call to helper, and not a call ripple can tell is one
    ("lib/phantom:phantom", "helper"): "type arguments, which JavaScript does not have",
    # jsconfig.json sets jsxFactory h: when Badge runs is not React's order, so no `renderer` edge
    ("lib/panel:Panel", "Badge"): "jsxFactory h, not React's runtime",
    # `import { top } from ".."` is the root directory, which has a package.json: its `main` (there is none, so index.js) is
    # what Node and the compiler read, and ripple does not read it, so the call is not followed
    ("lib/app:helper", "top"): "names the directory ./, whose entry its package.json",
}


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    return load_repo(REPO, cache_dir=tmp_path_factory.mktemp("cache"), lang="auto")


@pytest.fixture(scope="module")
def graph(loaded):
    return loaded.graph


def test_every_edge_is_the_truth_and_nothing_else(graph):
    assert {(e.kind, e.caller, e.callee) for e in graph.edges} == EDGES
    assert len(graph.edges) == len(EDGES)


def test_the_unresolved_calls_are_the_ones_the_source_cannot_settle(graph):
    found = {(u.caller, u.text): u.reason for u in graph.unresolved}
    assert set(found) == set(UNRESOLVED)
    for key, part in UNRESOLVED.items():
        assert part in found[key], key


def test_the_phantom_call_is_no_edge_to_the_function_it_names(graph):
    assert not [e for e in graph.edges if e.caller == "lib/phantom:phantom"]
    assert {c["symbol"] for c in callers(graph, "lib/app:helper")["callers"]} == {"lib/app:create", "lib/optional:viaOptional"}


def test_a_tsconfig_that_does_not_allow_javascript_gives_its_aliases_to_no_javascript_file(graph):
    """legacy/tsconfig.json maps @x/* to x/*, but it is not allowJs: legacy/use.js has the root jsconfig.json's aliases."""
    assert not [e for e in graph.edges if e.caller == "legacy/use:useThing"]
    assert not [u for u in graph.unresolved if u.caller == "legacy/use:useThing"]
    assert graph.external_reasons["a name imported from outside the repo"] == 1  # thing() from the package @x/thing


def test_the_repo_is_all_javascript_read_in_full(loaded):
    assert [(language.label, language.files, language.parse_errors) for language in loaded.languages] == [("JavaScript", 19, [])]
    assert loaded.warnings == []


def test_the_dual_format_pair_is_one_id_and_two_files_read(graph):
    assert graph.modules["esm/dual.js"] == "esm/dual" and graph.modules["esm/dual.mjs"] == "esm/dual.mjs"
    assert {"esm/dual:dual", "esm/dual.mjs:dual"} <= set(graph.symbols)
    assert graph.parse_errors == []


@pytest.mark.parametrize(
    ("query", "symbol"),
    [
        ("lib/app.js:helper", "lib/app:helper"),  # the path with its extension
        ("app:create", "lib/app:create"),  # the tail of a path
        ("esm/a.mjs:f", "esm/a:f"),
        ("src/sub/file.js", "src/sub/file"),  # a module, by its file
        ("src/sub/file", "src/sub/file"),
        ("lib/app:helper", "lib/app:helper"),
        ("esm/dual.mjs:dual", "esm/dual.mjs:dual"),
        ("src/index.js:entry", "src:entry"),  # an index file is its directory
        ("lib/badge.jsx:Badge", "lib/badge:Badge"),
    ],
)
def test_a_query_shaped_like_a_javascript_path_finds_its_one_symbol(graph, query, symbol):
    assert find_symbol(graph, query) == {"symbol": symbol}


def test_the_index_output_names_the_language_and_says_the_pair_was_read_and_not_that_it_failed(capsys):
    assert main(["index", str(REPO), "--reasons"]) == 0
    out = capsys.readouterr().out
    assert "JavaScript: 19 files\n" in out and "could not" not in out
    assert "note: dual-format files" in out and "esm/dual.mjs (beside esm/dual.js)" in out
    assert "23 resolved calls, 1 JSX edges, 6 unresolved calls, 2 calls to code outside the repo" in out
