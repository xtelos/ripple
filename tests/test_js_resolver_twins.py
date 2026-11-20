"""A JSON file beside code: `lib.json` beside `lib/index.js`, and `data.json` where `./data` names nothing else.

Each case was run through the real toolchain before the expected answer was written (Node 24.0.1 `createRequire(...).resolve`, with
NODE_PATH=src for the bare form; esbuild 0.28.0 `--bundle --metafile`; TypeScript 6.0.3 `--traceResolution`, moduleResolution bundler):

  - `lib` (baseUrl src) and `./lib`, with src/lib/index.js and src/lib.json: Node resolves src/lib.json, esbuild bundles src/lib.json,
    tsc resolves src/lib/index.js (with or without resolveJsonModule: it adds no extension for JSON). The same with index.ts and a
    TypeScript importer. So the three disagree, and no edge to the index is proven.
  - `@/x` with `paths: {"@/*": ["a/*", "b/*"]}`, a/x.json and b/x.js (or b/x/index.js): esbuild bundles a/x.json, tsc resolves b/x.js.
  - `./lib` with src/lib.js and src/lib.json: Node, esbuild and tsc all take src/lib.js. With src/lib.ts (a TypeScript importer):
    esbuild and tsc take src/lib.ts. Neither is a disagreement, so the edge stays.
  - `./lib/` (a trailing slash) with src/lib.json: Node, esbuild and tsc all take src/lib/index.js. The edge stays.
  - `./data` and `data` (baseUrl src), with only src/data.json: Node resolves src/data.json and esbuild bundles it (tsc resolves nothing,
    as it adds no extension for JSON). The file is data that holds no code, so the import is no repo module and no missing path, as
    `./data.json` written out always was: a package.
  - `./lib` with src/lib.cjs and src/lib.json: Node and esbuild take src/lib.json (neither adds .cjs).

Where tools disagree the answer is "unresolved" with a reason that names the twin. The graph-level tests need tree-sitter (the
`typescript` extra) and are skipped, with the reason, without it.
"""

import json

import pytest

from ripple.indexer import find_source_files
from ripple.profile import JAVASCRIPT, TYPESCRIPT
from ripple.ts_modules import ModuleMap

JS_BASE = {"jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "src"}})}
TS_BASE = {"tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "src"}})}
PATHS = {"jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["a/*", "b/*"]}}})}
EXPORT = "export function f() {}\n"


def needs_parser():
    pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
    pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")


def modules_of(repo, profile=JAVASCRIPT):
    return ModuleMap(repo, find_source_files(repo, profile))


def graph_of(repo, profile=JAVASCRIPT):
    needs_parser()
    from ripple.indexer import build_graph

    return build_graph(repo, profile)


def nothing_proven(graph, file, line):
    """The site is unresolved, and the graph holds no edge and no outside call."""
    reasons = {u.line: u.reason for u in graph.unresolved if u.file == file}
    assert line in reasons, f"{file}:{line} is not unresolved: {reasons}, edges {[(e.file, e.line, e.callee) for e in graph.edges]}, external {graph.external_calls}"
    assert graph.edges == [], [(e.file, e.line, e.callee) for e in graph.edges]
    assert graph.external_calls == 0, graph.external_reasons
    return reasons[line]


# ---- 1: a JSON twin of a directory beside its index

DIRECTORY_AND_TWIN = [
    # (config, importer, library file, specifier, profile)
    pytest.param(JS_BASE, "src/main.js", "src/lib/index.js", "lib", JAVASCRIPT, id="js-bare-baseurl"),
    pytest.param({}, "src/main.js", "src/lib/index.js", "./lib", JAVASCRIPT, id="js-relative"),
    pytest.param(TS_BASE, "src/main.ts", "src/lib/index.ts", "lib", TYPESCRIPT, id="ts-bare-baseurl"),
    pytest.param({}, "src/main.ts", "src/lib/index.ts", "./lib", TYPESCRIPT, id="ts-relative"),
]


@pytest.mark.parametrize("config, importer, library, spec, profile", DIRECTORY_AND_TWIN)
def test_an_import_with_no_slash_is_unresolved_beside_a_json_file_of_its_directory_name(make_repo, config, importer, library, spec, profile):
    repo = make_repo({**config, library: EXPORT, "src/lib.json": "{}", importer: ""})
    kind, why = modules_of(repo, profile).resolve(spec, importer)
    assert kind == "unresolved" and "src/lib.json" in why, (kind, why)


@pytest.mark.parametrize("config, importer, library, spec, profile", DIRECTORY_AND_TWIN)
def test_a_call_through_it_draws_no_edge_and_no_outside_call(make_repo, config, importer, library, spec, profile):
    repo = make_repo({**config, library: EXPORT, "src/lib.json": "{}", importer: f"import {{ f }} from '{spec}';\nf();\n"})
    why = nothing_proven(graph_of(repo, profile), importer, 2)
    assert "src/lib.json" in why, why


@pytest.mark.parametrize("config, importer, library, spec, profile", DIRECTORY_AND_TWIN)
def test_the_same_import_without_the_json_file_is_still_an_edge(make_repo, config, importer, library, spec, profile):
    repo = make_repo({**config, library: EXPORT, importer: f"import {{ f }} from '{spec}';\nf();\n"})
    assert modules_of(repo, profile).resolve(spec, importer) == ("repo", "src/lib")
    graph = graph_of(repo, profile)
    assert [(e.file, e.callee) for e in graph.edges] == [(importer, "src/lib:f")]


@pytest.mark.parametrize("config, importer, library, spec, profile", [case for case in DIRECTORY_AND_TWIN if case.id.endswith("relative")])
def test_a_trailing_slash_names_the_directory_whatever_json_file_is_beside_it(make_repo, config, importer, library, spec, profile):
    """Node, esbuild and tsc all take src/lib/index.js for `./lib/`: the JSON file is no rival to a directory written as one. (A bare
    name with a trailing slash is another answer, since esbuild bundles the file: see test_js_resolver_answers.py.)"""
    repo = make_repo({**config, library: EXPORT, "src/lib.json": "{}", importer: ""})
    assert modules_of(repo, profile).resolve(spec + "/", importer) == ("repo", "src/lib")


@pytest.mark.parametrize("code", ["b/x.js", "b/x/index.js"])
def test_an_earlier_place_that_holds_a_json_file_beats_a_later_place_that_holds_code(make_repo, code):
    """esbuild bundles a/x.json for `@/x` (paths tried in order); tsc resolves b/x.js, or b/x/index.js."""
    repo = make_repo({**PATHS, "a/x.json": "{}", code: "exports.f = 1;\n", "main.js": ""})
    kind, why = modules_of(repo).resolve("@/x", "main.js")
    assert kind == "unresolved" and "a/x.json" in why, (code, kind, why)


def test_a_json_file_in_a_later_place_does_not_hide_code_in_an_earlier_one(make_repo):
    repo = make_repo({**PATHS, "a/x.js": "exports.f = 1;\n", "b/x.json": "{}", "main.js": ""})
    assert modules_of(repo).resolve("@/x", "main.js") == ("repo", "a/x")


def test_a_json_file_beside_the_js_file_the_tools_all_take_is_no_disagreement(make_repo):
    """src/lib.js with src/lib.json: Node, esbuild and tsc all take lib.js."""
    repo = make_repo({"src/lib.js": "exports.f = 1;\n", "src/lib.json": "{}", "src/main.js": ""})
    assert modules_of(repo).resolve("./lib", "src/main.js") == ("repo", "src/lib")


def test_a_json_file_beside_the_ts_file_the_tools_all_take_is_no_disagreement(make_repo):
    """src/lib.ts with src/lib.json, from a TypeScript importer: esbuild and tsc both take lib.ts."""
    repo = make_repo({"src/lib.ts": EXPORT, "src/lib.json": "{}", "src/main.ts": ""})
    assert modules_of(repo, TYPESCRIPT).resolve("./lib", "src/main.ts") == ("repo", "src/lib")


def test_a_json_file_beside_a_module_file_node_does_not_add_an_extension_for_is_unresolved(make_repo):
    """Node's require adds .js, .json and .node: src/lib.mjs is no candidate, and it loads src/lib.json."""
    repo = make_repo({"src/lib.mjs": EXPORT, "src/lib.json": "{}", "src/main.js": ""})
    kind, why = modules_of(repo).resolve("./lib", "src/main.js")
    assert kind == "unresolved", (kind, why)


# ---- 2: a path that names only a data file

def test_an_extensionless_path_to_a_data_file_is_the_package_the_written_path_is(make_repo):
    """Node and esbuild load src/data.json for `./data`. The file holds no code, so no call through it runs code of the repo: a package,
    as `./data.json` is, and not a missing path."""
    repo = make_repo({"src/data.json": "{}", "src/main.js": ""})
    modules = modules_of(repo)
    assert modules.resolve("./data.json", "src/main.js") == ("package", "./data.json")
    assert modules.resolve("./data", "src/main.js") == ("package", "./data")
    repo = make_repo({"src/data.json": "{}", "src/main.ts": ""})
    assert modules_of(repo, TYPESCRIPT).resolve("./data", "src/main.ts") == ("package", "./data")


def test_a_bare_name_that_only_a_data_file_answers_stays_a_package(make_repo):
    repo = make_repo({**JS_BASE, "src/data.json": "{}", "src/main.js": ""})
    assert modules_of(repo).resolve("data", "src/main.js") == ("package", "data")


def test_a_data_file_beside_a_module_file_node_does_not_add_an_extension_for_is_still_the_data_file(make_repo):
    """Node and esbuild load src/lib.json for `./lib` beside src/lib.cjs (neither adds .cjs)."""
    repo = make_repo({"src/lib.cjs": "exports.f = 1;\n", "src/lib.json": "{}", "src/main.js": ""})
    assert modules_of(repo).resolve("./lib", "src/main.js") == ("package", "./lib")


def test_a_path_with_a_trailing_slash_does_not_name_the_data_file(make_repo):
    """`./data/` is the directory, which is not there: Node throws, and nothing is proven to be a file."""
    repo = make_repo({"src/data.json": "{}", "src/main.js": ""})
    assert modules_of(repo).resolve("./data/", "src/main.js") == ("missing", "./data/")


def test_a_path_that_names_nothing_is_still_missing_beside_other_json_files(make_repo):
    repo = make_repo({"src/data.json": "{}", "src/main.js": ""})
    modules = modules_of(repo)
    for spec in ("./nothing", "./dat", "./data2", "./data.json.json"):
        assert modules.resolve(spec, "src/main.js") == ("missing", spec), spec


def test_a_data_file_beside_a_declaration_file_of_the_same_name_is_still_unresolved(make_repo):
    """The declaration file holds code the compiler reads, which is not known to be the file the import names."""
    repo = make_repo({"src/data.d.ts": "export {}\n", "src/data.json": "{}", "src/main.js": ""})
    assert modules_of(repo).resolve("./data", "src/main.js")[0] == "unresolved"


def test_a_data_file_under_a_config_with_root_dirs_is_still_unresolved(make_repo):
    """`rootDirs` may put a source file of that name in another root, which the compiler tries before any JSON file."""
    roots = {"tsconfig.json": json.dumps({"compilerOptions": {"rootDirs": ["src", "gen"]}})}
    repo = make_repo({**roots, "src/data.json": "{}", "src/main.ts": ""})
    kind, why = modules_of(repo, TYPESCRIPT).resolve("./data", "src/main.ts")
    assert kind == "unresolved" and "rootDirs" in why, (kind, why)


def test_a_call_through_a_data_file_import_is_an_outside_call(make_repo):
    repo = make_repo({"src/data.json": "{}", "src/main.js": "import data from './data';\ndata.trim();\n"})
    graph = graph_of(repo)
    assert graph.external_calls == 1 and graph.edges == [] and graph.unresolved == []


# ---- the cache record: a graph is thrown away when the twin appears or goes


def load(repo, cache, profile=JAVASCRIPT):
    needs_parser()
    from ripple.cache import load_graph

    return load_graph(repo, cache, profile=profile)


def test_a_json_twin_that_appears_beside_a_directory_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"src/lib/index.js": EXPORT, "src/main.js": "import { f } from './lib';\nf();\n"})
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load(repo, cache)
    assert not hit and [(e.file, e.callee) for e in graph.edges] == [("src/main.js", "src/lib:f")]
    graph, hit = load(repo, cache)
    assert hit and len(graph.edges) == 1  # nothing changed: the record holds
    (repo / "src" / "lib.json").write_text("{}")
    graph, hit = load(repo, cache)
    assert not hit and graph.edges == [] and [u.line for u in graph.unresolved] == [2]
    graph, hit = load(repo, cache)
    assert hit and graph.edges == []
    (repo / "src" / "lib.json").unlink()
    graph, hit = load(repo, cache)
    assert not hit and len(graph.edges) == 1


def test_a_json_twin_that_appears_where_an_alias_looked_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({**JS_BASE, "src/lib/index.js": EXPORT, "src/main.js": "import { f } from 'lib';\nf();\n"})
    cache = tmp_path_factory.mktemp("cache")
    assert len(load(repo, cache)[0].edges) == 1
    (repo / "src" / "lib.json").write_text("{}")
    graph, hit = load(repo, cache)
    assert not hit and graph.edges == [] and [u.line for u in graph.unresolved] == [2]


def test_a_data_file_that_appears_where_an_extensionless_path_looked_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"src/main.js": "import data from './data';\ndata.trim();\n"})
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0  # nothing is there: missing, so the call is unresolved
    (repo / "src" / "data.json").write_text("{}")
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 1  # a data file: a package
    graph, hit = load(repo, cache)
    assert hit and graph.external_calls == 1
    (repo / "src" / "data.json").unlink()
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0


# ---- 3: the other files a resolver adds an extension for: .node (Node's require), .css (esbuild), and a directory's index.json
#
# Run the same way (Node 24.0.1, esbuild 0.28.0, tsc 6.0.3, Vite for the .node case):
#   - `./x` with only src/x.node: Node loads it, esbuild and Vite cannot resolve `./x`, tsc resolves nothing. A file Node loads and
#     a bundler does not is no missing path, and it holds code ripple does not read: unresolved, never a package.
#   - src/x.node beside src/x/index.js: Node loads x.node, esbuild and tsc take the index. src/x.js beside x.node: all take x.js.
#     src/x.json beside src/x.node: Node (.js, .json, .node) and esbuild take x.json.
#   - `./lib` with only src/lib/index.json: Node and esbuild load it, tsc resolves nothing. The answer is the directory's, as for every
#     directory (its package.json may pick another file). With src/lib/index.js beside it, all three take index.js: the edge stays.
#   - `./data` with only src/data.css: esbuild bundles it, Node throws. src/data.css beside src/data/index.js: esbuild bundles
#     data.css, Node and tsc take the index. src/data.css beside data.js: all take data.js; beside data.json: Node takes the JSON and
#     esbuild the CSS, and neither holds code.


def test_an_extensionless_path_to_a_native_addon_is_unresolved_never_missing(make_repo):
    repo = make_repo({"src/x.node": "", "src/main.js": "", "src/main.ts": ""})
    for importer, profile in (("src/main.js", JAVASCRIPT), ("src/main.ts", TYPESCRIPT)):
        kind, why = modules_of(repo, profile).resolve("./x", importer)
        assert kind == "unresolved" and "src/x.node" in why, (importer, kind, why)


def test_a_bare_name_that_only_a_native_addon_answers_is_unresolved(make_repo):
    repo = make_repo({**JS_BASE, "src/x.node": "", "src/main.js": ""})
    kind, why = modules_of(repo).resolve("x", "src/main.js")
    assert kind == "unresolved" and "src/x.node" in why, (kind, why)


def test_a_native_addon_beside_a_directory_index_is_what_node_loads(make_repo):
    repo = make_repo({"src/x.node": "", "src/x/index.js": EXPORT, "src/main.js": ""})
    kind, why = modules_of(repo).resolve("./x", "src/main.js")
    assert kind == "unresolved" and "src/x.node" in why, (kind, why)


def test_a_native_addon_beside_the_file_node_takes_first_is_no_disagreement(make_repo):
    repo = make_repo({"src/x.node": "", "src/x.js": EXPORT, "src/main.js": ""})
    assert modules_of(repo).resolve("./x", "src/main.js") == ("repo", "src/x")


def test_a_native_addon_beside_an_index_a_typescript_build_takes_is_no_disagreement(make_repo):
    """Node does not run a TypeScript importer, and esbuild, Vite and tsc all take src/x/index.ts."""
    repo = make_repo({"src/x.node": "", "src/x/index.ts": EXPORT, "src/main.ts": ""})
    assert modules_of(repo, TYPESCRIPT).resolve("./x", "src/main.ts") == ("repo", "src/x")


def test_a_json_file_beside_a_native_addon_is_the_data_file_every_tool_takes(make_repo):
    """Node's order is .js, .json, .node, and esbuild adds no .node."""
    repo = make_repo({"src/x.json": "{}", "src/x.node": "", "src/main.js": ""})
    assert modules_of(repo).resolve("./x", "src/main.js") == ("package", "./x")


def test_a_directory_whose_only_index_is_json_is_the_directorys_answer(make_repo):
    """Node and esbuild load src/lib/index.json; the answer is the one every directory gets, never a package or a missing path."""
    for spec, config, importer, profile in (("./lib", {}, "src/main.js", JAVASCRIPT), ("lib", JS_BASE, "src/main.js", JAVASCRIPT), ("./lib", {}, "src/main.ts", TYPESCRIPT)):
        repo = make_repo({**config, "src/lib/index.json": "{}", importer: ""})
        kind, why = modules_of(repo, profile).resolve(spec, importer)
        assert kind == "unresolved" and "src/lib/" in why, (spec, kind, why)


@pytest.mark.parametrize("importer, profile, index", [("src/main.js", JAVASCRIPT, "src/lib/index.js"), ("src/main.ts", TYPESCRIPT, "src/lib/index.ts")])
def test_an_index_json_beside_the_index_every_tool_takes_is_no_disagreement(make_repo, importer, profile, index):
    """Node's order is index.js then index.json, esbuild's index.ts, index.js, then index.json: with src/lib/index.js all load it."""
    repo = make_repo({"src/lib/index.json": "{}", index: EXPORT, importer: ""})
    assert modules_of(repo, profile).resolve("./lib", importer) == ("repo", "src/lib")


def test_an_extensionless_path_to_a_stylesheet_is_unresolved_never_missing(make_repo):
    """esbuild bundles src/data.css for `./data` and Node throws: `missing` is true for one tool and false for the other."""
    repo = make_repo({"src/data.css": "a {}", "src/main.js": "", "src/main.ts": ""})
    for importer, profile in (("src/main.js", JAVASCRIPT), ("src/main.ts", TYPESCRIPT)):
        kind, why = modules_of(repo, profile).resolve("./data", importer)
        assert kind == "unresolved" and "src/data.css" in why, (importer, kind, why)


@pytest.mark.parametrize("importer, profile, index", [("src/main.js", JAVASCRIPT, "src/data/index.js"), ("src/main.ts", TYPESCRIPT, "src/data/index.ts")])
def test_a_stylesheet_beside_a_directory_index_is_what_esbuild_bundles(make_repo, importer, profile, index):
    repo = make_repo({"src/data.css": "a {}", index: EXPORT, importer: ""})
    kind, why = modules_of(repo, profile).resolve("./data", importer)
    assert kind == "unresolved" and "src/data.css" in why, (importer, kind, why)


def test_a_stylesheet_in_an_earlier_place_beats_a_later_place_that_holds_code(make_repo):
    repo = make_repo({**PATHS, "a/x.css": "a {}", "b/x.js": "exports.f = 1;\n", "main.js": ""})
    kind, why = modules_of(repo).resolve("@/x", "main.js")
    assert kind == "unresolved" and "a/x.css" in why, (kind, why)


def test_a_stylesheet_beside_the_file_esbuild_takes_first_is_no_disagreement(make_repo):
    """esbuild's order is .tsx .ts .jsx .js .css .json: data.js beats data.css, and the written `./data.css` is a package as ever."""
    repo = make_repo({"src/data.css": "a {}", "src/data.js": "exports.f = 1;\n", "src/main.js": ""})
    assert modules_of(repo).resolve("./data", "src/main.js") == ("repo", "src/data")
    repo = make_repo({"src/sheet.css": "a {}", "src/main.js": ""})
    assert modules_of(repo).resolve("./sheet.css", "src/main.js") == ("package", "./sheet.css")


def test_a_stylesheet_beside_a_json_file_is_a_data_file_either_way(make_repo):
    """Node loads data.json and esbuild data.css: neither holds code, so it is no missing path and no repo module."""
    repo = make_repo({"src/data.css": "a {}", "src/data.json": "{}", "src/main.js": ""})
    assert modules_of(repo).resolve("./data", "src/main.js") == ("package", "./data")


def test_a_native_addon_that_appears_where_an_extensionless_path_looked_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"src/main.js": "import x from './x';\nx.go();\n"})
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load(repo, cache)
    assert not hit and not any("x.node" in u.reason for u in graph.unresolved)
    (repo / "src" / "x.node").write_text("")
    graph, hit = load(repo, cache)
    assert not hit and any("src/x.node" in u.reason for u in graph.unresolved)
    graph, hit = load(repo, cache)
    assert hit
    (repo / "src" / "x.node").unlink()
    graph, hit = load(repo, cache)
    assert not hit and not any("x.node" in u.reason for u in graph.unresolved)


def test_a_stylesheet_that_appears_beside_a_directory_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"src/data/index.js": EXPORT, "src/main.js": "import { f } from './data';\nf();\n"})
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load(repo, cache)
    assert not hit and len(graph.edges) == 1
    (repo / "src" / "data.css").write_text("a {}")
    graph, hit = load(repo, cache)
    assert not hit and graph.edges == [] and any("src/data.css" in u.reason for u in graph.unresolved)
    (repo / "src" / "data.css").unlink()
    graph, hit = load(repo, cache)
    assert not hit and len(graph.edges) == 1


# ---- 4: review round 1: the earlier `paths` targets, a link to code, the index step, esbuild's order, builtins and a bare file
#
# Run the same way (Node 24.0.1, esbuild 0.28.0, tsc 6.0.3, Vite 7 where an alias is not needed):
#   - `@/x` with `paths {"@/*": ["a/*", "b/*"]}`, a/x/index.json (or index.css) and b/x.js: esbuild bundles a/x/index.json, tsc resolves
#     b/x.js. With a/x/index.json and b/x/index.js: esbuild a/x/index.json, tsc b/x/index.js. With `{"@/x": ["a/x.json", "b/x.js"]}`:
#     esbuild and tsc both resolve a/x.json (with resolveJsonModule on or off), so no tool takes b/x.js. With a/x/readme.txt only in the
#     first place, esbuild and tsc take b/x.js: the edge stays.
#   - src/data.json a link to src/real.js, `./data`, `./data.json` and a bare `data`: Node's require, esbuild and Vite load src/real.js.
#     A link to src/real.json is data: all load it.
#   - src/lib/index.jsx beside src/lib/index.json: Node's require loads index.json, esbuild, Vite and tsc index.jsx. Beside index.node:
#     Node loads index.node. Beside index.js: all load index.js.
#   - src/lib.jsx beside src/lib.js (JavaScript importer): esbuild bundles lib.jsx, Node, Vite and tsc take lib.js. src/lib.ts beside
#     src/lib.tsx (TypeScript importer): esbuild bundles lib.tsx, Vite and tsc take lib.ts. src/lib/index.jsx beside index.js: the same.
#     src/lib.ts beside lib.jsx (TypeScript importer): every tool takes lib.ts.
#   - `events` under baseUrl src with src/events.css: Node takes the builtin, esbuild bundles src/events.css (its browser platform) or leaves
#     the builtin external (--platform=node), Vite takes the builtin. `foo`, an installed package, with foo.css at the root under baseUrl
#     ".": Node, Vite and tsc take node_modules/foo/index.js, esbuild bundles foo.css (either platform). With foo.json: the same, and the
#     JSON holds no code. So no reason may say Node loads the file or that esbuild always does.
#   - src/data, a file with no extension, `./data`: Node's require, esbuild and Vite load it as JavaScript. src/data.txt, `./data.txt`: Node's
#     require loads it as JavaScript (it ran: `{ marker: 1 }`), esbuild loads it as text, Vite loads nothing.

PATHS_TS = {"tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "allowJs": True, "paths": {"@/*": ["a/*", "b/*"]}}})}
PATHS_EXACT = {
    "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "allowJs": True, "resolveJsonModule": True, "paths": {"@/x": ["a/x.json", "b/x.js"]}}})
}
ROOT_BASE = {"jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}})}
PACKAGE = {"node_modules/foo/package.json": '{"name": "foo", "main": "index.js"}', "node_modules/foo/index.js": "exports.f = 1;\n"}


@pytest.mark.parametrize(
    "files, importer, profile",
    [
        pytest.param({"a/x/index.json": "{}", "b/x.js": "exports.f = 1;\n"}, "main.js", JAVASCRIPT, id="index-json"),
        pytest.param({"a/x/index.css": "a {}", "b/x.js": "exports.f = 1;\n"}, "main.js", JAVASCRIPT, id="index-css"),
        pytest.param({"a/x/index.json": "{}", "b/x.ts": EXPORT}, "main.ts", TYPESCRIPT, id="index-json-ts"),
        pytest.param({"a/x/index.json": "{}", "b/x/index.js": "exports.f = 1;\n"}, "main.js", JAVASCRIPT, id="index-json-over-index"),
    ],
)
def test_an_earlier_target_that_is_a_directory_holding_only_a_data_index_beats_a_later_place_that_holds_code(make_repo, files, importer, profile):
    repo = make_repo({**PATHS_TS, **files, importer: ""})
    kind, why = modules_of(repo, profile).resolve("@/x", importer)
    assert kind == "unresolved" and "a/x/index." in why, (kind, why)


def test_an_earlier_target_that_is_itself_a_json_file_beats_a_later_place_that_holds_code(make_repo):
    """esbuild and tsc both resolve a/x.json, so the edge to b/x.js is wrong: no tool takes it."""
    repo = make_repo({**PATHS_EXACT, "a/x.json": "{}", "b/x.js": "exports.f = 1;\n", "main.js": ""})
    kind, why = modules_of(repo).resolve("@/x", "main.js")
    assert kind == "unresolved" and "a/x.json" in why, (kind, why)


def test_an_earlier_directory_that_holds_nothing_a_bundler_takes_does_not_hide_a_later_place(make_repo):
    repo = make_repo({**PATHS_TS, "a/x/readme.txt": "notes", "b/x.js": "exports.f = 1;\n", "main.js": ""})
    assert modules_of(repo).resolve("@/x", "main.js") == ("repo", "b/x")


@pytest.mark.parametrize("spec, config", [("./data", {}), ("./data.json", {}), ("data", JS_BASE)])
def test_a_json_file_that_is_a_link_to_code_of_the_repo_is_unresolved_never_a_package(make_repo, spec, config):
    """Node's require, esbuild and Vite load src/real.js (by the real path) for src/data.json, a link to it."""
    repo = make_repo({**config, "src/real.js": "exports.f = 1;\n", "src/main.js": ""})
    (repo / "src" / "data.json").symlink_to("real.js")
    kind, why = modules_of(repo).resolve(spec, "src/main.js")
    assert kind == "unresolved" and "src/real.js" in why, (spec, kind, why)


def test_a_json_file_that_is_a_link_to_another_data_file_is_still_a_package(make_repo):
    repo = make_repo({"src/real.json": "{}", "src/main.js": ""})
    (repo / "src" / "data.json").symlink_to("real.json")
    assert modules_of(repo).resolve("./data", "src/main.js") == ("package", "./data")
    assert modules_of(repo).resolve("./data.json", "src/main.js") == ("package", "./data.json")


@pytest.mark.parametrize("twin", ["index.json", "index.node"])
def test_an_index_beside_a_data_index_node_loads_first_is_unresolved(make_repo, twin):
    """Node's require loads src/lib/index.json (or index.node) for `./lib`, esbuild, Vite and tsc src/lib/index.jsx."""
    repo = make_repo({"src/lib/index.jsx": EXPORT, "src/lib/" + twin: "{}", "src/main.js": ""})
    kind, why = modules_of(repo).resolve("./lib", "src/main.js")
    assert kind == "unresolved" and "src/lib/" + twin in why, (kind, why)


@pytest.mark.parametrize(
    "files, importer, profile, names",
    [
        pytest.param({"src/lib.jsx": EXPORT, "src/lib.js": "exports.f = 1;\n"}, "src/main.js", JAVASCRIPT, ("src/lib.jsx", "src/lib.js"), id="jsx-beside-js"),
        pytest.param({"src/lib.ts": EXPORT, "src/lib.tsx": EXPORT}, "src/main.ts", TYPESCRIPT, ("src/lib.ts", "src/lib.tsx"), id="ts-beside-tsx"),
        pytest.param({"src/lib/index.jsx": EXPORT, "src/lib/index.js": "exports.f = 1;\n"}, "src/main.js", JAVASCRIPT, ("src/lib/index.jsx", "src/lib/index.js"), id="index-jsx-beside-index-js"),
    ],
)
def test_two_code_files_esbuilds_order_and_the_compilers_pick_differently_between_are_unresolved(make_repo, files, importer, profile, names):
    repo = make_repo({**files, importer: ""})
    kind, why = modules_of(repo, profile).resolve("./lib", importer)
    assert kind == "unresolved" and all(name in why for name in names), (kind, why)


def test_a_jsx_file_beside_a_ts_file_is_no_disagreement_for_a_typescript_importer(make_repo):
    """esbuild, Vite and tsc all take src/lib.ts."""
    repo = make_repo({"src/lib.ts": EXPORT, "src/lib.jsx": EXPORT, "src/main.ts": ""})
    assert modules_of(repo, TYPESCRIPT).resolve("./lib", "src/main.ts") == ("repo", "src/lib")


@pytest.mark.parametrize("twin", ["events.css", "events.node"])
def test_a_reason_for_a_node_builtin_beside_a_file_a_resolver_adds_an_extension_to_says_only_what_the_tools_do(make_repo, twin):
    """`events` is the builtin to Node and Vite, and esbuild takes src/events.css for its browser platform and leaves the builtin
    external for `--platform=node`: no reason may say Node loads the file."""
    repo = make_repo({**JS_BASE, "src/" + twin: "", "src/main.js": ""})
    kind, why = modules_of(repo).resolve("events", "src/main.js")
    assert kind == "unresolved" and "src/" + twin in why and "may" in why and "Node builtin" in why, (kind, why)


@pytest.mark.parametrize("twin", ["foo.css", "foo.node"])
def test_an_installed_package_beside_a_stylesheet_or_a_native_addon_under_the_base_url_is_unresolved_with_a_true_reason(make_repo, twin):
    """esbuild bundles foo.css for `foo`, Node, Vite and tsc take node_modules/foo/index.js; Node loads no foo.node here either."""
    repo = make_repo({**ROOT_BASE, **PACKAGE, twin: "", "main.js": ""})
    kind, why = modules_of(repo).resolve("foo", "main.js")
    assert kind == "unresolved" and twin in why and "may" in why, (twin, kind, why)


def test_an_installed_package_beside_a_json_file_under_the_base_url_stays_a_package(make_repo):
    """Node, Vite and tsc take the package, esbuild takes foo.json: neither runs code of the repo."""
    repo = make_repo({**ROOT_BASE, **PACKAGE, "foo.json": "{}", "main.js": ""})
    assert modules_of(repo).resolve("foo", "main.js") == ("package", "foo")


def test_a_file_with_no_extension_is_unresolved_never_a_package(make_repo):
    """Node's require, esbuild and Vite load src/data as JavaScript."""
    repo = make_repo({"src/data": "exports.marker = 1;\n", "src/main.js": ""})
    kind, why = modules_of(repo).resolve("./data", "src/main.js")
    assert kind == "unresolved" and "src/data" in why and "no extension" in why, (kind, why)


def test_a_file_with_no_extension_in_a_package_directory_is_still_a_package(make_repo):
    repo = make_repo({"node_modules/foo/LICENSE": "MIT\n", "src/main.js": ""})
    assert modules_of(repo).resolve("../node_modules/foo/LICENSE", "src/main.js") == ("package", "../node_modules/foo/LICENSE")


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN-WRONG: Node's require loads any extension it has no loader for as JavaScript (it ran src/data.txt), esbuild loads it as "
    "text and Vite loads nothing, so `./data.txt` is not known to be a data file; ripple answers package, as it does for a written .svg or .png",
)
def test_a_written_file_of_an_extension_node_runs_as_javascript_is_not_a_package(make_repo):
    repo = make_repo({"src/data.txt": "exports.marker = 1;\n", "src/main.js": ""})
    assert modules_of(repo).resolve("./data.txt", "src/main.js")[0] == "unresolved"


def test_a_data_index_that_appears_in_an_earlier_target_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({**PATHS_TS, "b/x.js": "export function f() {}\n", "main.js": "import { f } from '@/x';\nf();\n"})
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load(repo, cache)
    assert not hit and len(graph.edges) == 1
    (repo / "a" / "x").mkdir(parents=True)
    (repo / "a" / "x" / "index.json").write_text("{}")
    graph, hit = load(repo, cache)
    assert not hit and graph.edges == [] and any("a/x/index.json" in u.reason for u in graph.unresolved)
    (repo / "a" / "x" / "index.json").unlink()
    graph, hit = load(repo, cache)
    assert not hit and len(graph.edges) == 1


def test_a_link_that_appears_where_a_data_file_looked_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"src/real.js": "exports.go = function go() {};\n", "src/data.json": "{}", "src/main.js": "import data from './data';\ndata.go();\n"})
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 1  # data: a package
    (repo / "src" / "data.json").unlink()
    (repo / "src" / "data.json").symlink_to("real.js")
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0 and any("src/real.js" in u.reason for u in graph.unresolved)


def test_an_index_json_that_appears_beside_an_index_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"src/lib/index.jsx": "export function f() {}\n", "src/main.js": "import { f } from './lib';\nf();\n"})
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load(repo, cache)
    assert not hit and len(graph.edges) == 1
    (repo / "src" / "lib" / "index.json").write_text("{}")
    graph, hit = load(repo, cache)
    assert not hit and graph.edges == [] and any("src/lib/index.json" in u.reason for u in graph.unresolved)


def test_a_file_with_no_extension_that_appears_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"src/main.js": "import data from './data';\ndata.go();\n"})
    cache = tmp_path_factory.mktemp("cache")
    graph, hit = load(repo, cache)
    assert not hit and not any("no extension" in u.reason for u in graph.unresolved)
    (repo / "src" / "data").write_text("exports.go = function go() {};\n")
    graph, hit = load(repo, cache)
    assert not hit and any("no extension" in u.reason for u in graph.unresolved)
    (repo / "src" / "data").unlink()
    graph, hit = load(repo, cache)
    assert not hit and not any("no extension" in u.reason for u in graph.unresolved)
