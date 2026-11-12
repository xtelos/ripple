"""JavaScript beside TypeScript, and beside nothing: each language is its own graph, so an import from one into the other is
unresolved with a reason (never a package, never an edge), and a name both define is the registry's ambiguity.

These need tree-sitter and are skipped, with the reason, without it.
"""

import json
from dataclasses import fields, replace

import pytest
from conftest import FIXTURES

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple import registry  # noqa: E402
from ripple.cache import load_repo, profile_key  # noqa: E402
from ripple.cli import main  # noqa: E402
from ripple.profile import GO, JAVASCRIPT, RUST, TYPESCRIPT, LanguageProfile  # noqa: E402
from ripple.queries import callees, callers, find_symbol  # noqa: E402

WAREHOUSE = FIXTURES / "ts" / "warehouse-ts"
FROM_TS = "`{spec}` names {file}, a JavaScript file, whose calls ripple does not follow from TypeScript"
FROM_JS = "`{spec}` names {file}, a TypeScript file, whose calls ripple does not follow from JavaScript"


def reasons(graph, caller):
    return {u.text: u.reason for u in graph.unresolved if u.caller == caller}


# ---- TypeScript importing JavaScript (behaviour 1)


def test_a_typescript_file_that_imports_a_js_file_by_any_route_has_unresolved_calls_and_no_external_ones(make_repo):
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["lib/*"]}}}),
            "lib/x.js": "export function f() { return 1; }\n",
            "src/main.ts": """
            import { f } from "../lib/x.js";
            import { f as g } from "../lib/x";
            import { f as h } from "@lib/x";
            export function main() { return f() + g() + h(); }
            """,
        }
    )
    result = load_repo(repo, lang="typescript")
    graph = result.graph
    assert [e for e in graph.edges if e.caller == "src/main:main"] == []
    assert graph.external_calls == 0 and graph.external_reasons == {}  # at HEAD all three were "a name imported from outside the repo"
    why = reasons(graph, "src/main:main")
    assert set(why) == {"f", "g", "h"}
    assert FROM_TS.format(spec="../lib/x.js", file="lib/x.js") in why["f"]
    assert FROM_TS.format(spec="../lib/x", file="lib/x.js") in why["g"]
    assert FROM_TS.format(spec="@lib/x", file="lib/x.js") in why["h"]


# ---- JavaScript importing TypeScript (behaviour 2)


def test_a_js_file_that_imports_an_extensionless_name_with_only_a_ts_file_is_unresolved_never_external(make_repo):
    repo = make_repo(
        {
            "src/x.ts": "export function f(): number { return 1; }\n",
            "src/main.js": 'import { f } from "./x";\nexport function main() { return f(); }\n',
        }
    )
    graph = load_repo(repo, lang="javascript").graph
    assert [e for e in graph.edges if e.caller == "src/main:main"] == []
    assert graph.external_calls == 0
    assert reasons(graph, "src/main:main")["f"].endswith(FROM_JS.format(spec="./x", file="src/x.ts"))


# ---- the same id in both languages (behaviour 3)

CLASH = {
    "src/util.ts": "export function total(): number { return 1; }\n",
    "src/util.js": "export function total() { return 2; }\n",
    "src/main.ts": 'import { total } from "./util";\nexport function useTs() { return total(); }\n',
    "src/legacy.js": 'import { total } from "./util.js";\nexport function useJs() { return total(); }\n',
}


def test_a_ts_and_a_js_file_with_one_id_warn_naming_both_files_and_typescript_keeps_the_id(make_repo, capsys):
    repo = make_repo(CLASH)
    assert main(["index", str(repo)]) == 3
    out = capsys.readouterr().out
    assert "warning: both languages define 2 ids: ripple kept the TypeScript symbol and left out the JavaScript one" in out
    assert "src/util (src/util.js, src/util.ts)" in out and "src/util:total (src/util.js, src/util.ts)" in out
    result = load_repo(repo)
    assert result.graph.symbols["src/util:total"].file == "src/util.ts"
    refusal = find_symbol(result.graph, "src/util:total")
    assert "both languages define 'src/util:total'" in refusal["error"] and "does not answer for it" in refusal["error"]


def test_asking_for_typescript_alone_gives_the_answer_it_always_gave(make_repo, capsys):
    repo = make_repo(CLASH)
    assert main(["callers", "src/util:total", "--repo", str(repo), "--lang", "typescript"]) == 0
    out = capsys.readouterr().out
    assert "src/main:useTs" in out and "warning" not in out
    result = load_repo(repo, lang="typescript")
    assert result.warnings == [] and [c["symbol"] for c in callers(result.graph, "src/util:total")["callers"]] == ["src/main:useTs"]


def test_a_js_importer_of_util_js_beside_util_ts_is_unresolved_because_the_compiler_and_node_disagree(make_repo):
    """The compiler reads `./util.js` as util.ts, Node and the bundlers run util.js: either edge could be the wrong one, so
    neither is drawn. The JavaScript graph still holds util.js, whose `total` is the symbol the clash warning names."""
    repo = make_repo(CLASH)
    graph = load_repo(repo, lang="javascript").graph
    assert callees(graph, "src/legacy:useJs")["callees"] == []
    why = reasons(graph, "src/legacy:useJs")["total"]
    assert "`./util.js` names src/util.ts to the compiler, which tries TypeScript files first, and src/util.js to Node and the bundlers" in why
    assert graph.symbols["src/util:total"].file == "src/util.js"


# ---- a TypeScript repo with the JavaScript language registered (behaviour 4)


def test_a_typescript_only_repo_answers_the_same_with_and_without_javascript_registered():
    with_js = load_repo(WAREHOUSE, lang="typescript")
    registered = dict(registry.LANGUAGES)
    del registry.LANGUAGES["javascript"]
    try:
        without_js = load_repo(WAREHOUSE, lang="typescript")
    finally:  # put them all back in their order: it is the precedence, and a later test reads it
        registry.LANGUAGES.clear()
        registry.LANGUAGES.update(registered)
    assert with_js.graph.to_dict() == without_js.graph.to_dict()
    assert json.dumps(with_js.graph.to_dict()) == json.dumps(without_js.graph.to_dict())


def test_the_warehouse_fixture_keeps_its_numbers_and_gains_the_jest_config_as_a_javascript_file(capsys):
    assert main(["index", str(WAREHOUSE)]) == 0
    out = capsys.readouterr().out
    assert "93 resolved calls, 21 JSX edges, 40 unresolved calls, 60 calls to code outside the repo" in out
    assert "  TypeScript: 21 files\n  JavaScript: 1 file\n" in out  # jest.config.cjs
    assert "could not" not in out


# ---- choosing a language, and the labels (behaviours 5 and 6)

MIXED = {
    "src/a.ts": "export function a(): number { return 1; }\n",
    "src/b.tsx": "export function b() { return 2; }\n",
    "web/c.js": "export function c() { return 3; }\n",
    "web/d.jsx": "export function d() { return 4; }\n",
    "web/e.mjs": "export function e() { return 5; }\n",
    "tool.py": "def p():\n    return 6\n",
}


def test_each_language_reads_only_its_own_files_and_auto_reads_them_in_registry_order(make_repo):
    repo = make_repo(MIXED)
    js = load_repo(repo, lang="javascript")
    ts = load_repo(repo, lang="typescript")
    auto = load_repo(repo)
    assert sorted(js.graph.modules) == ["web/c.js", "web/d.jsx", "web/e.mjs"]
    assert sorted(ts.graph.modules) == ["src/a.ts", "src/b.tsx"]
    assert [language.name for language in auto.languages] == ["python", "typescript", "javascript"]
    assert [language.files for language in auto.languages] == [1, 2, 3]
    assert sorted(auto.graph.modules) == sorted(["tool.py", *ts.graph.modules, *js.graph.modules])


def test_the_index_output_counts_each_language_by_its_label(make_repo, capsys):
    repo = make_repo(MIXED)
    assert main(["index", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "languages:\n  Python: 1 file\n  TypeScript: 2 files\n  JavaScript: 3 files\n" in out
    assert main(["index", str(repo), "--lang", "javascript"]) == 0
    assert "languages:\n  JavaScript: 3 files\n" in capsys.readouterr().out


def test_the_registry_says_which_languages_have_path_ids_and_how_a_path_is_folded():
    assert registry.path_suffixes() == (
        *TYPESCRIPT.source_suffixes,
        *JAVASCRIPT.source_suffixes,
        *GO.source_suffixes,
        *RUST.source_suffixes,
    )
    assert not registry.LANGUAGES["python"].path_ids
    assert [registry.module_id(f) for f in ("lib/app.js", "src/index.mjs", "a.tsx", "index.cjs", "src/index")] == [
        "lib/app",
        "src",
        "a",
        "index",
        "src",
    ]
    assert [registry.stem(f) for f in ("lib/app.js", "a.cts", "tool.py")] == ["lib/app", "a", "tool"]


# ---- the cache keys


def test_javascript_has_its_own_cache_key_and_a_profile_field_is_not_added_to_move_the_others():
    """cache.profile_key hashes every field of a LanguageProfile, so a new field changes the key of TypeScript whatever value it
    is given. The id shape lives on registry.Language (path_ids) for that reason. If a field is added on purpose, every cache of
    every language but Python is rebuilt once: bump CACHE_FORMAT with it and update this count."""
    assert len(fields(LanguageProfile)) == 30
    assert profile_key(JAVASCRIPT).startswith("javascript-") and profile_key(TYPESCRIPT).startswith("typescript-")
    assert profile_key(JAVASCRIPT) != profile_key(TYPESCRIPT) and profile_key(registry.LANGUAGES["python"].profile) == ""
    assert profile_key(replace(TYPESCRIPT)) == profile_key(TYPESCRIPT)


# ---- the call f < a > (b)


JS_PHANTOM = "import { f } from './f.js';\nexport function g(a, b) { return f < a > (b); }\n"
TS_PHANTOM = JS_PHANTOM.replace("./f.js", "./f")
F = "export function f() { return 1; }\n"


@pytest.mark.parametrize("suffix", [".js", ".jsx", ".mjs"])
def test_in_a_javascript_file_f_lt_a_gt_b_is_two_comparisons_and_no_call(make_repo, suffix):
    repo = make_repo({"f.js": F, f"g{suffix}": JS_PHANTOM})
    graph = load_repo(repo, lang="javascript").graph
    assert [e for e in graph.edges if e.callee == "f:f"] == []
    assert "type arguments, which JavaScript does not have" in reasons(graph, "g:g")["f"]


def test_the_same_text_in_a_typescript_file_is_a_call_with_type_arguments(make_repo):
    repo = make_repo({"f.ts": F.replace("f()", "f<T = number>(): number"), "g.ts": TS_PHANTOM})
    graph = load_repo(repo, lang="typescript").graph
    assert [(e.caller, e.callee) for e in graph.edges] == [("g:g", "f:f")]


def test_check_in_a_javascript_repo_is_a_refusal_that_names_javascript_not_a_traceback(make_repo, capsys):
    repo = make_repo({"a.js": F})
    assert main(["check", "--repo", str(repo)]) == 2
    out = capsys.readouterr().out
    assert "does not know how to run JavaScript tests" in out and "pass the command" in out


# ---- an `export *` from a file of the other language leaves the export set unknown

STARS = {
    "barrel.ts": "export * from './legacy.js';\nexport * from './modern';\n",
    "legacy.js": "export function foo() {}\nexport function old() {}\n",
    "modern.ts": "export function foo() {}\nexport function fresh() {}\n",
    "user.ts": "import { foo, old, fresh } from './barrel';\nexport function run() {\n  foo();\n  old();\n  fresh();\n}\n",
}


def test_a_name_looked_up_through_a_barrel_that_stars_the_other_language_is_unresolved_never_taken_from_another_star(make_repo):
    """The reviewer's probe p9. legacy.js and modern.ts both export foo, which the compiler reports as ambiguous (TS2308); and
    legacy.js could export `fresh` too. ripple does not read legacy.js here, so nothing the barrel does not declare is known."""
    graph = load_repo(make_repo(STARS), lang="typescript").graph
    assert [e for e in graph.edges if e.caller == "user:run"] == []
    why = reasons(graph, "user:run")
    assert set(why) == {"foo", "old", "fresh"}
    for name in why:
        assert f"'{name}' may come from an `export *` of module barrel that ripple does not follow" in why[name]
        assert FROM_TS.format(spec="./legacy.js", file="legacy.js") in why[name]


def test_the_same_barrel_in_javascript_and_a_lone_star_onto_a_ts_file(make_repo):
    repo = make_repo(
        {
            "barrel.js": "export * from './t';\nexport * from './j';\n",
            "t.ts": "export function bar() {}\n",
            "j.js": "export function bar() {}\nexport function only_j() {}\n",
            "lone.js": "export * from './t';\n",
            "user.js": "import { bar, only_j } from './barrel';\nimport { bar as lone } from './lone';\nexport function go() {\n  bar();\n  only_j();\n  lone();\n}\n",
        }
    )
    graph = load_repo(repo, lang="javascript").graph
    assert [e for e in graph.edges if e.caller == "user:go"] == []
    why = reasons(graph, "user:go")
    assert FROM_JS.format(spec="./t", file="t.ts") in why["bar"] and FROM_JS.format(spec="./t", file="t.ts") in why["only_j"]
    assert "is not exported by module" not in why["lone"]  # the old reason said the name was missing; it is not known


def test_a_name_the_barrel_declares_itself_and_a_star_of_the_same_language_are_still_edges(make_repo):
    repo = make_repo(
        {
            "barrel.ts": "export * from './legacy.js';\nexport function foo() {}\n",
            "legacy.js": "export function foo() {}\n",
            "plain.ts": "export * from './a';\n",
            "a.ts": "export function g() {}\n",
            "user.ts": "import { foo } from './barrel';\nimport { g } from './plain';\nexport function run() {\n  foo();\n  g();\n}\n",
        }
    )
    graph = load_repo(repo, lang="typescript").graph
    assert sorted(e.callee for e in graph.edges if e.caller == "user:run") == ["a:g", "barrel:foo"]


# ---- compiled output next to its sources

def test_a_directory_a_config_names_as_out_dir_is_skipped_at_any_depth_and_listed(make_repo):
    """The reviewer's probe p7: packages/core/dist/math.js is what tsc emitted from src/math.ts."""
    repo = make_repo(
        {
            "packages/core/tsconfig.json": json.dumps({"compilerOptions": {"outDir": "dist"}}),
            "packages/core/src/math.ts": "export function clamp() {}\n",
            "packages/core/dist/math.js": "export function clamp() {}\n",
            "packages/app/dist/other.js": "export function keep() {}\n",
            "packages/app/src/use.ts": "export function use() {}\n",
        }
    )
    result = load_repo(repo)
    assert find_symbol(result.graph, "clamp") == {"symbol": "packages/core/src/math:clamp"}
    assert "packages/core/dist" in result.graph.skipped_dirs and "packages/core/dist/math.js" not in result.graph.modules
    assert "packages/app/dist/other.js" in result.graph.modules  # no config names that one, so it is source as it was


def test_an_out_dir_through_extends_counts_and_one_that_is_the_config_directory_or_above_it_does_not(make_repo):
    repo = make_repo(
        {
            "base.json": json.dumps({"compilerOptions": {"outDir": "out"}}),
            "a/tsconfig.json": json.dumps({"extends": "../base.json"}),
            "a/x.ts": "export const x = 1;\n",
            "out/a.js": "export const a = 1;\n",  # base.json is at the root, so its outDir is the root's out
            "b/tsconfig.json": json.dumps({"compilerOptions": {"outDir": "."}}),
            "b/y.ts": "export const y = 1;\n",
            "b/y.js": "export const y = 1;\n",
            "c/tsconfig.json": json.dumps({"compilerOptions": {"outDir": "../elsewhere"}}),
            "c/z.ts": "export const z = 1;\n",
            "elsewhere/e.js": "export const e = 1;\n",
        }
    )
    graph = load_repo(repo).graph
    assert "out/a.js" not in graph.modules and "out" in graph.skipped_dirs
    assert "b/y.js" in graph.modules  # in-place emit: nothing says which files are the output
    assert "elsewhere/e.js" not in graph.modules and "elsewhere" in graph.skipped_dirs


# ---- names the TypeScript grammar takes for keywords, in a JavaScript file


@pytest.mark.parametrize("name", ["abstract", "using"])
def test_a_call_after_a_name_the_grammar_takes_for_a_keyword_is_not_an_edge(make_repo, name):
    """The reviewer's probe p6: `abstract.foo();` is an error holding `abstract.` and then, to tree-sitter, a call of foo."""
    repo = make_repo({f"{name}.js": f"const {name} = {{ foo() {{}} }};\nexport function k1() {{\n  {name}.foo();\n}}\nfunction foo() {{}}\n"})
    result = load_repo(repo, lang="javascript")
    assert [e for e in result.graph.edges if e.caller == f"{name}:k1"] == []
    assert "inside or right after a syntax error" in reasons(result.graph, f"{name}:k1")["foo"]
    assert f"{name}.js" in result.graph.parse_errors  # still listed: the file was damaged


KEYWORDS = "declare namespace module type readonly override satisfies as is keyof infer accessor public private protected global asserts out".split()


def test_no_other_typescript_contextual_keyword_used_as_a_name_makes_a_call_in_a_javascript_file(make_repo):
    files = {"base.js": "export function foo() {}\n"}
    for word in KEYWORDS:
        files[f"k_{word}.js"] = f"import {{ foo }} from './base.js';\nconst {word} = {{ foo() {{}}, bar: {{ foo() {{}} }} }};\nexport function k() {{\n  {word}.foo();\n  {word}.bar.foo();\n}}\n"
    graph = load_repo(make_repo(files), lang="javascript").graph
    for word in KEYWORDS:
        assert [e for e in graph.edges if e.caller == f"k_{word}:k" and e.callee == "base:foo"] == [], word
