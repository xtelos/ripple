"""Answers of the JavaScript and TypeScript module resolver that claimed more than the source proves.

Each case was run through the real toolchain before the expected answer was written (Node 24.0.1; TypeScript 5.9.3 `resolveModuleName`;
esbuild 0.27.7 as the bundler). Where they disagree the answer is "unresolved", the one answer ripple may always give:

  - `import 'lib/'` under `baseUrl: "src"`, with src/lib.js and src/lib/index.js: `tsc` gives src/lib/index.js, Node's require (with
    NODE_PATH=src) gives src/lib/index.js, esbuild bundles src/lib.js. With only src/lib.js, `tsc` resolves nothing, Node throws and
    esbuild bundles src/lib.js. With only src/lib/index.js all three give the index.
  - a TypeScript `import '/src/a'` with `paths: {"/*": ["./*"]}`: `tsc` gives src/a.ts; Node reads the path from the file system root.
    An absolute path and a `file:` URL name a file by where it is on a machine, in either language.
  - `import 'data:text/javascript,import {f} from "file:///.../a.mjs"; ...'` run in Node reaches a.mjs: the module is the text of the
    URL, and it may import a file of the repo, so a call into it is not an outside call.
  - `import Child from './Child.vue'` (and `.svelte`, `.astro`) names a file of the repo that ripple does not read.
  - `import 'node:events'` with `paths: {"node:events": ["./src/events.ts"]}`: Node takes the builtin, `tsc` gives src/events.ts, esbuild
    (platform browser, its default) bundles src/events.ts. A bare `events` is answered the same way already.
  - `require('./.lib/b')`, `require('./dist/x')`: Node runs .lib/b.js and dist/x.js, files the walk skips. `./a.mjs?v=1`, `./a.mjs#x` and
    `./%61.mjs` run a.mjs in Node (ESM reads the specifier as a URL).

A path that names nothing at all is still "missing", a package is still a package, and a call that stays outside the repo is still external.
The graph-level tests need tree-sitter (the `typescript` extra) and are skipped, with the reason, without it.
"""

import json

import pytest

from ripple.indexer import find_source_files
from ripple.profile import JAVASCRIPT, TYPESCRIPT
from ripple.ts_modules import ModuleMap


def needs_parser():
    pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
    pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")


def graph_of(repo, profile=JAVASCRIPT):
    needs_parser()
    from ripple.indexer import build_graph

    return build_graph(repo, profile)


def modules_of(repo, profile=JAVASCRIPT):
    return ModuleMap(repo, find_source_files(repo, profile))


def nothing_proven(graph, file, line):
    """The site is unresolved, and the graph holds no edge and no outside call."""
    reasons = {u.line: u.reason for u in graph.unresolved if u.file == file}
    assert line in reasons, f"{file}:{line} is not unresolved: {reasons}, edges {[(e.file, e.line, e.callee) for e in graph.edges]}, external {graph.external_calls}"
    assert graph.edges == [], [(e.file, e.line, e.callee) for e in graph.edges]
    assert graph.external_calls == 0, graph.external_reasons
    return reasons[line]


def edges(graph):
    return [(e.file, e.caller, e.callee) for e in graph.edges]


# ---- 1: a bare alias with a trailing slash

SLASH_JS = {"jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "src"}})}
SLASH_TS = {"tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "src", "paths": {"@/*": ["*"]}}})}


def test_a_trailing_slash_alias_with_a_file_of_that_name_and_a_directory_index_is_unresolved_in_javascript(make_repo):
    repo = make_repo(
        {**SLASH_JS, "src/lib.js": "export function f() {}\n", "src/lib/index.js": "export function f() {}\n", "src/main.mjs": "import { f } from 'lib/';\nf();\n"}
    )
    assert "which one it names is not known" in nothing_proven(graph_of(repo), "src/main.mjs", 2)
    assert modules_of(repo).resolve("lib/", "src/main.mjs")[0] == "unresolved"
    assert modules_of(repo).resolve("lib", "src/main.mjs") == ("repo", "src/lib")  # no slash: the file, as before


def test_a_trailing_slash_alias_with_only_a_file_of_that_name_is_unresolved_never_that_file_or_a_package(make_repo):
    repo = make_repo({**SLASH_JS, "src/lib.js": "export function f() {}\n", "src/main.mjs": "import { f } from 'lib/';\nf();\n"})
    nothing_proven(graph_of(repo), "src/main.mjs", 2)


def test_a_trailing_slash_alias_is_unresolved_in_typescript_by_baseurl_and_by_paths(make_repo):
    repo = make_repo(
        {
            **SLASH_TS,
            "src/lib.ts": "export function f() {}\n",
            "src/lib/index.ts": "export function f() {}\n",
            "src/a.ts": "import { f } from 'lib/';\nf();\n",
            "src/b.ts": "import { f } from '@/lib/';\nf();\n",
        }
    )
    graph = graph_of(repo, TYPESCRIPT)
    assert {(u.file, u.line) for u in graph.unresolved} == {("src/a.ts", 2), ("src/b.ts", 2)}
    assert graph.edges == [] and graph.external_calls == 0
    modules = modules_of(repo, TYPESCRIPT)
    assert modules.resolve("lib", "src/a.ts") == ("repo", "src/lib")
    assert modules.resolve("@/lib", "src/a.ts") == ("repo", "src/lib")


def test_a_trailing_slash_alias_with_only_the_directory_keeps_the_index_edge(make_repo):
    """tsc, Node's require and esbuild all take src/lib/index.js here."""
    repo = make_repo({**SLASH_JS, "src/lib/index.js": "export function f() {}\n", "src/main.mjs": "import { f } from 'lib/';\nf();\n"})
    graph = graph_of(repo)
    assert edges(graph) == [("src/main.mjs", "src/main", "src/lib:f")]


# ---- 2: a path or URL that may name a file of the repo, and a file of the repo that ripple does not read


def test_a_typescript_absolute_path_is_unresolved_with_and_without_a_paths_entry(make_repo):
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"/*": ["./*"]}}}),
            "src/a.ts": "export function f() {}\n",
            "src/main.ts": "import { f } from '/src/a';\nf();\n",
            "src/other.ts": "import { f } from '/somewhere/else';\nf();\n",
        }
    )
    graph = graph_of(repo, TYPESCRIPT)
    assert {(u.file, u.line) for u in graph.unresolved} == {("src/main.ts", 2), ("src/other.ts", 2)}
    assert graph.edges == [] and graph.external_calls == 0
    plain = make_repo({"main.ts": ""})
    assert modules_of(plain, TYPESCRIPT).resolve("/abs/path", "main.ts")[0] == "unresolved"


@pytest.mark.parametrize("spec", ["file:///abs/x", "FILE:///abs/x", " file:///abs/x", "fi\tle:///abs/x", "/abs/x", "\\x2fabs/x"])
def test_a_typescript_file_url_an_absolute_path_and_an_escape_are_unresolved_as_in_javascript(make_repo, spec):
    repo = make_repo({"main.ts": "", "main.js": ""})
    for importer, profile in (("main.ts", TYPESCRIPT), ("main.js", JAVASCRIPT)):
        kind, why = modules_of(repo, profile).resolve(spec, importer)
        assert kind == "unresolved" and "may name a file of this repo" in why, (importer, spec, kind, why)


@pytest.mark.parametrize("profile_file", [("main.mjs", JAVASCRIPT), ("main.ts", TYPESCRIPT)])
def test_a_data_url_import_is_unresolved_never_an_outside_call(make_repo, profile_file):
    """Run in Node: the module is the text of the URL and it imported a.mjs by its file: URL, so `g()` ran repo code."""
    name, profile = profile_file
    repo = make_repo({"a.mjs": "export function f() {}\n"})
    data = f"data:text/javascript,import {{ f }} from 'file://{repo}/a.mjs'; export function g() {{ f(); }}"
    (repo / name).write_text(f"import {{ g }} from {json.dumps(data)};\ng();\n")
    why = nothing_proven(graph_of(repo, profile), name, 2)
    assert "data: URL" in why
    modules = modules_of(repo, profile)
    for spec in ("data:text/javascript,export default 1", "DATA:text/javascript,1", " data:text/javascript,1"):
        assert modules.resolve(spec, name)[0] == "unresolved"
    for spec in ("https://example.com/x.js", "node:fs", "react"):
        assert modules.resolve(spec, name) == ("package", spec)  # other schemes and names are outside the repo, as before


@pytest.mark.parametrize("ext", [".vue", ".svelte", ".astro"])
@pytest.mark.parametrize("importer", ["src/main.js", "src/main.mjs", "src/main.ts", "src/main.tsx"])
def test_a_relative_import_of_a_component_file_ripple_does_not_read_is_unresolved_never_a_package(make_repo, ext, importer):
    repo = make_repo({f"src/Child{ext}": "<script>export default {}</script>\n", importer: f"import Child from './Child{ext}';\nChild.render();\n"})
    profile = TYPESCRIPT if importer.endswith((".ts", ".tsx")) else JAVASCRIPT
    why = nothing_proven(graph_of(repo, profile), importer, 2)
    assert "ripple does not read" in why and f"Child{ext}" in why


def test_an_alias_of_a_component_file_a_skipped_file_or_an_unread_one_is_unresolved_never_a_package(make_repo):
    repo = make_repo(
        {
            "jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}),
            "src/Child.vue": "<script></script>\n",
            "src/.gen/g.js": "exports.f = 1;\n",
            "dist/x.js": "exports.f = 1;\n",
            "main.js": "",
        }
    )
    modules = modules_of(repo)
    for spec in ("@/Child.vue", "@/Child", "@/.gen/g", "dist/x"):
        assert modules.resolve(spec, "main.js")[0] == "unresolved", spec
    assert modules.resolve("react", "main.js") == ("package", "react")
    assert modules.resolve("@/nothing", "main.js") == ("package", "@/nothing")


def test_a_component_file_in_node_modules_and_a_data_file_are_still_packages(make_repo):
    repo = make_repo({"node_modules/p/C.vue": "<script></script>\n", "src/data.json": "{}", "src/main.js": ""})
    modules = modules_of(repo)
    assert modules.resolve("../node_modules/p/C.vue", "src/main.js") == ("package", "../node_modules/p/C.vue")
    assert modules.resolve("./data.json", "src/main.js") == ("package", "./data.json")


@pytest.mark.parametrize("profile_file", [("main.mjs", JAVASCRIPT), ("main.ts", TYPESCRIPT)])
def test_node_scheme_mapped_into_the_repo_by_paths_is_unresolved_like_the_bare_name(make_repo, profile_file):
    """esbuild for the browser bundles src/events.ts for `node:events` and for `events`, Node takes the builtin for both."""
    name, profile = profile_file
    config = "jsconfig.json" if profile is JAVASCRIPT else "tsconfig.json"
    target = "src/events.js" if profile is JAVASCRIPT else "src/events.ts"
    repo = make_repo(
        {
            config: json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"node:events": ["./" + target], "events": ["./" + target]}}}),
            target: "export function on() {}\n",
            name: "import { on } from 'node:events';\non();\n",
        }
    )
    assert "Node builtin" in nothing_proven(graph_of(repo, profile), name, 2)
    modules = modules_of(repo, profile)
    assert modules.resolve("node:events", name)[0] == modules.resolve("events", name)[0] == "unresolved"
    assert modules.resolve("node:fs", name) == ("package", "node:fs")  # nothing maps it into the repo


def test_node_scheme_named_by_a_file_of_the_baseurl_is_unresolved_and_any_other_is_a_package(make_repo):
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}),
            "node:util.ts": "export function format() {}\n",
            "ok.ts": "import { format } from 'node:util';\nformat();\n",
            "other.ts": "import { join } from 'node:path';\njoin();\n",
        }
    )
    graph = graph_of(repo, TYPESCRIPT)
    assert [u.file for u in graph.unresolved if "Node builtin" in u.reason] == ["ok.ts"]
    assert graph.edges == [] and graph.external_calls == 1  # node:path


# ---- 3: a path that names a file ripple does not read is no missing path


@pytest.mark.parametrize("spec", ["./.lib/b", "./.lib/b.js", "./.lib/index", "./.lib/", "./dist/x", "./dist/x.js"])
def test_an_extensionless_or_index_path_into_a_skipped_directory_is_unresolved_never_missing(make_repo, spec):
    repo = make_repo({".lib/b.js": "exports.f = 1;\n", ".lib/index.js": "exports.g = 1;\n", "dist/x.js": "exports.f = 1;\n", "main.js": ""})
    kind, why = modules_of(repo).resolve(spec, "main.js")
    assert kind == "unresolved" and "ripple does not read" in why, (spec, kind, why)


def test_a_typescript_import_that_names_a_skipped_source_by_its_built_extension_is_unresolved_never_missing(make_repo):
    repo = make_repo({".lib/b.ts": "export function f() {}\n", "dist/x.ts": "export function f() {}\n", "main.ts": ""})
    modules = modules_of(repo, TYPESCRIPT)
    for spec in ("./.lib/b.js", "./.lib/b", "./dist/x", "./dist/x.js"):
        assert modules.resolve(spec, "main.ts")[0] == "unresolved", spec


def test_an_extensionless_path_that_a_resolver_gives_a_code_extension_to_is_not_missing(make_repo):
    """A type-checker adds `.d.ts` and Vite adds `.mjs`: each names a file that holds code ripple does not read."""
    repo = make_repo({"types.d.ts": "export {}\n", "m.mjs": "export {}\n", "main.js": "", "main.ts": ""})
    for spec in ("./types", "./m"):
        kind, why = modules_of(repo).resolve(spec, "main.js")
        assert kind == "unresolved", (spec, kind, why)
    assert modules_of(repo, TYPESCRIPT).resolve("./types", "main.ts")[0] == "unresolved"


def test_an_extensionless_path_to_a_data_file_is_the_package_the_written_path_is(make_repo):
    """Node's require, Vite and esbuild load data.json for `./data`, so `missing` is not true. A file that holds no code is classed by one
    rule for a relative path and an alias, and the written `./data.json` is a package: so is `./data` (see test_js_resolver_twins.py)."""
    repo = make_repo({"data.json": "{}", "main.js": ""})
    assert modules_of(repo).resolve("./data", "main.js") == ("package", "./data")


def test_a_path_that_names_nothing_is_still_missing(make_repo):
    repo = make_repo({".lib/b.js": "", "src/a.js": "", "main.js": "", "main.ts": ""})
    modules = modules_of(repo)
    for spec in ("./nothing", "./.lib/nothing", "./.lib/b.css", "./src/zzz/a"):
        assert modules.resolve(spec, "main.js") == ("missing", spec), spec
    assert modules_of(repo, TYPESCRIPT).resolve("./nothing", "main.ts") == ("missing", "./nothing")


def test_no_reason_for_an_unread_star_of_a_skipped_path_says_it_is_missing(make_repo):
    repo = make_repo(
        {".lib/b.js": "exports.f = 1;\n", "mid.mjs": "export * from './.lib/b';\n", "main.mjs": "import { f } from './mid.mjs';\nf();\n"}
    )
    graph = graph_of(repo)
    reasons = [u.reason for u in graph.unresolved if u.file == "main.mjs"]
    assert reasons and not any("is missing" in reason for reason in reasons), reasons
    assert graph.edges == [] and graph.external_calls == 0


QUERY_SPECS = ["./a.mjs?v=1", "./a.mjs#x", "./%61.mjs", "./sub/%2e%2e/a.mjs", "./a.mjs?worker", "./nothing?url", "../a.mjs?x"]


@pytest.mark.parametrize("spec", QUERY_SPECS)
def test_a_relative_specifier_with_a_query_a_fragment_or_percent_encoding_is_unresolved_never_missing(make_repo, spec):
    """Node ran a.mjs for each of `./a.mjs?v=1`, `./a.mjs#x`, `./%61.mjs` and `./sub/%2e%2e/a.mjs`."""
    repo = make_repo({"a.mjs": "export function f() {}\n", "sub/m.mjs": "", "main.mjs": ""})
    for importer in ("main.mjs", "sub/m.mjs"):
        kind, why = modules_of(repo).resolve(spec, importer)
        assert kind == "unresolved" and "a URL" in why, (importer, spec, kind, why)


def test_a_query_import_draws_no_edge_and_no_outside_call_in_either_language(make_repo):
    repo = make_repo(
        {
            "a.mjs": "export function f() {}\n",
            "main.mjs": "import { f } from './a.mjs?v=1';\nimport { f as g } from './%61.mjs';\nf();\ng();\n",
            "a.ts": "export function t() {}\n",
            "main.ts": "import { t } from './a?raw';\nt();\n",
        }
    )
    graph = graph_of(repo)
    assert {(u.file, u.line) for u in graph.unresolved} == {("main.mjs", 3), ("main.mjs", 4)}
    assert graph.edges == [] and graph.external_calls == 0
    ts = graph_of(repo, TYPESCRIPT)
    assert {(u.file, u.line) for u in ts.unresolved} == {("main.ts", 2)}
    assert ts.edges == [] and ts.external_calls == 0


def test_a_specifier_without_those_characters_is_read_as_before(make_repo):
    repo = make_repo({"a.mjs": "export function f() {}\n", "main.mjs": "import { f } from './a.mjs';\nf();\n"})
    assert edges(graph_of(repo)) == [("main.mjs", "main", "a:f")]


# ---- review round 1: one rule for what a path finds, an alias with a query, a data twin of a directory, and the cache


ALIAS_CONFIG = {"jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}})}


def test_an_alias_and_a_relative_path_classify_the_file_they_find_alike(make_repo):
    """Data and a package's file are outside the repo's code (a package), a file ripple does not read that holds code is unresolved."""
    repo = make_repo(
        {
            **ALIAS_CONFIG,
            "src/data.json": "{}",
            "src/main.css": "a {}",
            "src/Child.vue": "<script></script>\n",
            "src/.gen/g.js": "exports.f = 1;\n",
            "src/node_modules/app/a.js": "exports.f = 1;\n",
            "src/types.d.ts": "export {}\n",
            "src/main.js": "",
        }
    )
    modules = modules_of(repo)
    for name, kind in [
        ("data.json", "package"),
        ("main.css", "package"),
        ("node_modules/app/a.js", "package"),
        ("Child.vue", "unresolved"),
        (".gen/g.js", "unresolved"),
        (".gen/g", "unresolved"),
        ("types.d.ts", "unresolved"),
    ]:
        relative, alias = modules.resolve("./" + name, "src/main.js")[0], modules.resolve("@/" + name, "src/main.js")[0]
        assert relative == alias == kind, (name, relative, alias)
    assert modules.resolve("./data.json", "src/main.js") == ("package", "./data.json")
    assert modules.resolve("@/data.json", "src/main.js") == ("package", "@/data.json")


def test_a_paths_entry_into_node_modules_keeps_a_package_a_package(make_repo):
    """tsc: `lodash` is node_modules/lodash/index.js (external). The older Angular and Ionic shape."""
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"*": ["node_modules/*", "src/types/*"]}}}),
            "node_modules/lodash/index.js": "exports.f = 1;\n",
            "node_modules/react/jsx-runtime.js": "exports.g = 1;\n",
            "src/main.ts": "import { f } from 'lodash';\nimport { g } from 'react/jsx-runtime';\nf();\ng();\n",
        }
    )
    modules = modules_of(repo, TYPESCRIPT)
    assert modules.resolve("lodash", "src/main.ts") == ("package", "lodash")
    assert modules.resolve("react/jsx-runtime", "src/main.ts") == ("package", "react/jsx-runtime")
    graph = graph_of(repo, TYPESCRIPT)
    assert graph.external_calls == 2 and graph.edges == [] and graph.unresolved == []


@pytest.mark.parametrize("profile_file", [("src/main.mjs", JAVASCRIPT), ("src/main.ts", TYPESCRIPT)])
def test_an_alias_with_a_query_a_fragment_or_percent_encoding_is_unresolved_never_an_outside_call(make_repo, profile_file):
    """esbuild bundles src/a.mjs for `@/a.mjs?v=1`, src/b.js for `@/b#frag` and src/c.mjs for `src/c.mjs?x`; tsc resolves none of them."""
    name, profile = profile_file
    repo = make_repo({**ALIAS_CONFIG, "src/a.mjs": "export function f() {}\n", "src/b.js": "", "src/c.mjs": "", name: ""})
    specs = ["@/a.mjs?v=1", "@/b#frag", "src/c.mjs?x", "@/%61.mjs", "@/a.mjs?worker&url"]
    modules = modules_of(repo, profile)
    for spec in specs:
        kind, why = modules.resolve(spec, name)
        assert kind == "unresolved" and "alias" in why, (spec, kind, why)
    (repo / name).write_text("import { f } from '@/a.mjs?v=1';\nf();\n")
    nothing_proven(graph_of(repo, profile), name, 2)


def test_a_package_name_with_a_query_is_still_a_package_under_an_alias(make_repo):
    repo = make_repo({**ALIAS_CONFIG, "src/main.js": ""})
    modules = modules_of(repo)
    for spec in ("react?x", "lodash#frag", "react/jsx-runtime?raw"):
        assert modules.resolve(spec, "src/main.js") == ("package", spec)


def test_a_query_on_a_bare_name_no_config_maps_is_a_package(make_repo):
    repo = make_repo({"src/a.mjs": "", "src/main.mjs": ""})  # no config governs the file: no alias
    assert modules_of(repo).resolve("@/a.mjs?v=1", "src/main.mjs") == ("package", "@/a.mjs?v=1")


@pytest.mark.parametrize("twin", ["lib.json", "lib.node", "lib.d.ts", "lib.mjs", "lib.vue"])
def test_a_trailing_slash_alias_is_unresolved_beside_a_file_a_resolver_adds_an_extension_to(make_repo, twin):
    """esbuild bundles src/lib.json for `lib/` under baseUrl `src` (no lib/index.js in the bundle); tsc and Node's require take the index."""
    repo = make_repo({**SLASH_JS, "src/lib/index.js": "export function f() {}\n", "src/" + twin: "{}", "src/main.mjs": "import { f } from 'lib/';\nf();\n"})
    why = nothing_proven(graph_of(repo), "src/main.mjs", 2)
    assert "trailing slash" in why or "names" in why


# ---- the cache record of what the file system was asked beyond the walk


def cache_of(tmp_path_factory):
    return tmp_path_factory.mktemp("cache")


def load(repo, cache, profile=JAVASCRIPT):
    needs_parser()
    from ripple.cache import load_graph

    return load_graph(repo, cache, profile=profile)


def test_a_file_that_appears_where_an_alias_looked_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    """Index with nothing at `@/Comp`, create src/Comp.vue, index again: the answer changes. Then delete it: it changes back."""
    repo = make_repo({**ALIAS_CONFIG, "src/main.mjs": "import Comp from '@/Comp';\nexport function main() { Comp.render(); }\n"})
    cache = cache_of(tmp_path_factory)
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 1
    graph, hit = load(repo, cache)
    assert hit and graph.external_calls == 1  # nothing changed: the record holds, and the graph is served
    (repo / "src" / "Comp.vue").write_text("<script></script>\n")
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0 and [u.line for u in graph.unresolved] == [2]
    graph, hit = load(repo, cache)
    assert hit and graph.external_calls == 0
    (repo / "src" / "Comp.vue").unlink()
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 1


def test_build_output_that_appears_under_an_alias_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({**ALIAS_CONFIG, "main.mjs": "import x from 'dist/out';\nx.run();\n"})
    cache = cache_of(tmp_path_factory)
    assert load(repo, cache)[0].external_calls == 1
    (repo / "dist").mkdir()
    (repo / "dist" / "out.js").write_text("export default {};\n")
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0 and [u.line for u in graph.unresolved] == [2]


def test_a_file_that_appears_where_a_relative_path_looked_changes_the_stated_reason(make_repo, tmp_path_factory):
    repo = make_repo({"main.mjs": "import { f } from './.lib/b';\nf();\n"})
    cache = cache_of(tmp_path_factory)
    graph, _ = load(repo, cache)
    assert not any("names .lib/b.js" in u.reason for u in graph.unresolved)
    (repo / ".lib").mkdir()
    (repo / ".lib" / "b.js").write_text("exports.f = 1;\n")
    graph, hit = load(repo, cache)
    assert not hit and any("names .lib/b.js" in u.reason for u in graph.unresolved)
    (repo / ".lib" / "b.js").unlink()
    graph, hit = load(repo, cache)
    assert not hit and not any("names .lib/b.js" in u.reason for u in graph.unresolved)


def test_a_data_file_that_appears_where_a_relative_path_looked_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"main.mjs": "import logo from './logo.svg';\nlogo.trim();\n"})
    cache = cache_of(tmp_path_factory)
    assert load(repo, cache)[0].external_calls == 0  # nothing is there: the call is unresolved
    (repo / "logo.svg").write_text("<svg/>")
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 1  # a file that holds no code: a package
    (repo / "logo.svg").unlink()
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0


def test_a_file_of_a_package_directory_that_is_deleted_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    # a path through node_modules is asked about by _exists alone (_directory never probes it), so this is the one
    # case that fails if _exists stops recording what it asked
    repo = make_repo(
        {"main.mjs": "import data from './node_modules/foo/data.json';\ndata.trim();\n", "node_modules/foo/data.json": "{}"}
    )
    cache = cache_of(tmp_path_factory)
    assert load(repo, cache)[0].external_calls == 1  # a file of a package
    (repo / "node_modules" / "foo" / "data.json").unlink()
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0  # nothing is there now


def test_a_directory_that_appears_where_an_alias_looked_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({**ALIAS_CONFIG, "main.mjs": "import gen from '@/gen';\ngen.go();\n"})
    cache = cache_of(tmp_path_factory)
    assert load(repo, cache)[0].external_calls == 1
    (repo / "src" / "gen").mkdir(parents=True)  # no source in it: the walk and the fingerprint see nothing
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0 and [u.line for u in graph.unresolved] == [2]
    (repo / "src" / "gen").rmdir()
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 1


def test_a_link_outside_the_repo_that_is_pointed_back_in_is_not_served_from_a_stale_cache(make_repo, tmp_path_factory):
    repo = make_repo({"a.mjs": "export function f() {}\n", "main.mjs": ""})
    outside = repo.parent / (repo.name + "-links")
    (outside / "elsewhere").mkdir(parents=True)
    (outside / "back").symlink_to(outside / "elsewhere")
    (repo / "main.mjs").write_text(f"import {{ f }} from '../{outside.name}/back/a.mjs';\nf();\n")
    cache = cache_of(tmp_path_factory)
    graph, _ = load(repo, cache)
    assert graph.external_calls == 1 and graph.unresolved == []
    (outside / "back").unlink()
    (outside / "back").symlink_to(repo)
    graph, hit = load(repo, cache)
    assert not hit and graph.external_calls == 0 and [u.line for u in graph.unresolved] == [2]


def test_a_long_lived_index_notices_a_file_that_appears_beside_the_walk(make_repo, tmp_path_factory):
    needs_parser()
    from ripple.cache import RepoIndex

    repo = make_repo({**ALIAS_CONFIG, "main.mjs": "import Comp from '@/Comp';\nComp.render();\n"})
    index = RepoIndex(repo, cache_of(tmp_path_factory), profile=JAVASCRIPT)
    assert index.graph().external_calls == 1
    (repo / "src").mkdir()
    (repo / "src" / "Comp.vue").write_text("<script></script>\n")
    assert index.graph().external_calls == 0


def test_a_record_written_without_probes_is_not_read_as_one_with_none(make_repo, tmp_path_factory):
    from ripple.cache import cache_file

    repo = make_repo({"main.mjs": "import { f } from './nothing';\nf();\n"})
    cache = cache_of(tmp_path_factory)
    load(repo, cache)
    assert load(repo, cache)[1]
    path = cache_file(repo.resolve(), cache, JAVASCRIPT)
    saved = json.loads(path.read_text())
    assert "probes" in saved
    del saved["probes"]
    path.write_text(json.dumps(saved))
    assert not load(repo, cache)[1]  # rebuilt, and written again with its record
    assert "probes" in json.loads(path.read_text())
    saved = json.loads(path.read_text())
    saved["probes"] = {"fs": {"nothing": ["f"]}, "reenter": {}}  # a path that is not a file now, in a record that says it is
    path.write_text(json.dumps(saved))
    assert not load(repo, cache)[1]


def test_a_python_record_is_written_as_it_always_was(make_repo, tmp_path_factory):
    from ripple.cache import cache_file, load_graph

    repo = make_repo({"a.py": "def f():\n    pass\n"})
    cache = cache_of(tmp_path_factory)
    load_graph(repo, cache)
    saved = json.loads(cache_file(repo.resolve(), cache).read_text())
    assert set(saved) == {"format", "files", "graph"}


def test_the_probe_record_is_sorted_relative_and_holds_no_path_of_this_machine(make_repo):
    needs_parser()
    from ripple.indexer import build_graph
    from ripple.ts_modules import ProbeRecorder

    repo = make_repo({**ALIAS_CONFIG, "main.mjs": ""})
    (repo / "main.mjs").write_text(
        f"import a from '@/Comp';\nimport b from './.lib/b';\nimport c from '../{repo.name}/x.mjs';\na.f();\nb.f();\nc.f();\n"
    )
    with ProbeRecorder() as recorder:
        build_graph(repo, JAVASCRIPT)
    record = recorder.record()
    assert list(record["fs"]) == sorted(record["fs"]) and list(record["reenter"]) == sorted(record["reenter"])
    assert record["reenter"] == {f"../{repo.name}/x.mjs": True}
    assert not any(key.startswith("/") or key.startswith("..") for key in record["fs"])
    assert str(repo) not in json.dumps(record) and str(repo.resolve()) not in json.dumps(record)
    assert record["fs"]["src/Comp.vue"] == []  # nothing is there, and it was asked
