"""Which file a JavaScript import names, and how a TypeScript import tells a JavaScript file from a package. No parser needed.

Each language is its own graph, so a ModuleMap holds the files of one of them and answers for an importer written in it.
A file of the other language that an import names is not in the map: it is "unresolved" with a reason, never "package".
"""

import json

import pytest

from ripple.cache import fingerprint
from ripple.indexer import find_source_files
from ripple.profile import JAVASCRIPT, TYPESCRIPT
from ripple.ts_modules import JS_SUFFIXES, SUFFIXES, ModuleMap, config_files, load_tsconfig


def mapper(make_repo, files, profile=JAVASCRIPT, **kwargs):
    """A ModuleMap of the profile's files among `files` (a list of paths, or a dict of path to source)."""
    repo = make_repo(files if isinstance(files, dict) else {name: "" for name in files})
    return ModuleMap(repo, find_source_files(repo, profile), **kwargs), repo


def test_the_javascript_profile_agrees_with_the_suffixes_the_resolver_knows():
    assert JAVASCRIPT.source_suffixes == JS_SUFFIXES and TYPESCRIPT.source_suffixes == SUFFIXES
    assert JAVASCRIPT.excluded_suffixes == () and JAVASCRIPT.front_end == TYPESCRIPT.front_end
    assert JAVASCRIPT.name == "javascript" and set(JAVASCRIPT.config_files) == {"jsconfig.json", "tsconfig.json"}


@pytest.mark.parametrize(
    ("specifier", "module"),
    [
        ("./lib/app.js", "lib/app"),
        ("./lib/app", "lib/app"),  # no extension: .js, then .jsx
        ("./lib", "lib"),  # a directory is its index file
        ("./ui/Badge", "ui/Badge"),  # .jsx is tried after .js
        ("./esm/a.mjs", "esm/a"),  # .mjs and .cjs are named in full
        ("./esm/b.cjs", "esm/b"),
    ],
)
def test_a_javascript_import_is_the_file_as_written_or_with_js_then_jsx_added(make_repo, specifier, module):
    modules, _ = mapper(make_repo, ["main.js", "lib/app.js", "lib/index.js", "ui/Badge.jsx", "esm/a.mjs", "esm/b.cjs"])
    assert modules.resolve(specifier, "main.js") == ("repo", module)


def test_mjs_and_cjs_are_never_guessed_for_an_import_with_no_extension(make_repo):
    modules, _ = mapper(make_repo, ["main.js", "esm/a.mjs", "esm/b.cjs"])
    assert modules.resolve("./esm/a", "main.js") == ("missing", "./esm/a")
    assert modules.resolve("./esm/b", "main.js") == ("missing", "./esm/b")


def test_js_is_tried_before_jsx_when_a_name_has_both(make_repo):
    modules, _ = mapper(make_repo, ["main.js", "ui/Badge.js", "ui/Badge.jsx"])
    assert modules.resolve("./ui/Badge", "main.js") == ("repo", "ui/Badge")
    assert modules.modules["ui/Badge.js"] == "ui/Badge" and modules.modules["ui/Badge.jsx"] == "ui/Badge.jsx"
    assert modules.resolve("./ui/Badge.jsx", "main.js") == ("repo", "ui/Badge.jsx")


def test_a_typescript_importer_takes_the_x_ts_the_compiler_reads_and_a_javascript_one_refuses_to_pick_between_x_ts_and_x_js(make_repo):
    """A map of both languages' files (what a joint pass would hold). The compiler reads `./x` and `./x.js` as x.ts for any
    importer, and a TypeScript importer takes that (x.js is what the build makes of it). Node and the bundlers run x.js for
    a JavaScript importer, so which file a JavaScript importer names is not known: unresolved, never the compiler's guess."""
    repo = make_repo({"x.ts": "", "x.js": "", "a.ts": "", "a.js": ""})
    modules = ModuleMap(repo, [repo / "x.ts", repo / "x.js", repo / "a.ts", repo / "a.js"])
    assert modules.modules["x.ts"] == "x" and modules.modules["x.js"] == "x.js"  # a.ts keeps the id, x.js has its path
    assert modules.resolve("./x.js", "a.ts") == ("repo", "x")
    assert modules.resolve("./x", "a.ts") == ("repo", "x")
    for spec in ("./x.js", "./x"):
        kind, why = modules.resolve(spec, "a.js")
        assert kind == "unresolved" and "x.ts to the compiler" in why and "x.js to Node and the bundlers" in why, spec


def test_an_import_of_the_root_through_dot_dot_is_the_roots_index_in_both_languages(make_repo):
    js, _ = mapper(make_repo, ["index.js", "test/a.test.js", "src/sub/deep.js"])
    assert js.resolve("..", "test/a.test.js") == ("repo", "index")
    assert js.resolve("../", "test/a.test.js") == ("repo", "index")
    assert js.resolve("../..", "src/sub/deep.js") == ("repo", "index")
    assert js.resolve("../../..", "src/sub/deep.js") == ("package", "../../..")  # above the repo root


def test_the_same_root_import_in_a_typescript_repo(tmp_path):
    (tmp_path / "test").mkdir()
    for name in ("index.ts", "test/a.test.ts"):
        (tmp_path / name).write_text("")
    modules = ModuleMap(tmp_path, find_source_files(tmp_path, TYPESCRIPT))
    assert modules.resolve("..", "test/a.test.ts") == ("repo", "index")
    assert modules.resolve("../", "test/a.test.ts") == ("repo", "index")
    assert modules.resolve("../index", "test/a.test.ts") == ("repo", "index")


def test_a_typescript_file_importing_a_javascript_file_is_unresolved_with_the_reason_and_never_a_package(make_repo):
    modules, _ = mapper(make_repo, ["src/main.ts", "src/x.js", "src/y.jsx", "src/z.mjs", "src/dir/index.js"], TYPESCRIPT)
    here = "src/main.ts"
    why = "`{spec}` names {file}, a JavaScript file, whose calls ripple does not follow from TypeScript"
    for spec, file in [
        ("./x.js", "src/x.js"),
        ("./x", "src/x.js"),
        ("./y.jsx", "src/y.jsx"),
        ("./y", "src/y.jsx"),
        ("./z.mjs", "src/z.mjs"),
        ("./dir", "src/dir/index.js"),
    ]:
        assert modules.resolve(spec, here) == ("unresolved", why.format(spec=spec, file=file))


def test_a_javascript_file_importing_a_typescript_file_is_unresolved_with_the_reason_and_never_a_package(make_repo):
    modules, _ = mapper(make_repo, ["src/main.js", "src/x.ts", "src/y.tsx", "src/dir/index.ts"], JAVASCRIPT)
    here = "src/main.js"
    why = "`{spec}` names {file}, a TypeScript file, whose calls ripple does not follow from JavaScript"
    for spec, file in [("./x", "src/x.ts"), ("./y", "src/y.tsx"), ("./dir", "src/dir/index.ts"), ("./x.js", "src/x.ts")]:
        assert modules.resolve(spec, here) == ("unresolved", why.format(spec=spec, file=file))


def test_a_declaration_file_is_not_a_file_of_the_other_language(make_repo):
    modules, _ = mapper(make_repo, ["src/main.js", "src/types.d.ts"], JAVASCRIPT)
    assert modules.resolve("./types.d", "src/main.js") == ("missing", "./types.d")  # src/types.d.ts is declarations only


def test_a_json_or_css_file_is_still_a_package_for_either_language(make_repo):
    for profile, here in ((JAVASCRIPT, "src/main.js"), (TYPESCRIPT, "src/main.ts")):
        modules, _ = mapper(make_repo, [here, "src/data.json", "src/styles.css"], profile)
        assert modules.resolve("./data.json", here) == ("package", "./data.json")
        assert modules.resolve("./styles.css", here) == ("package", "./styles.css")


def test_a_paths_alias_or_a_base_url_that_reaches_a_javascript_file_is_unresolved_from_typescript(make_repo):
    config = json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@u/*": ["src/*"]}}})
    modules, _ = mapper(make_repo, {"tsconfig.json": config, "src/main.ts": "", "src/u.js": "", "src/t.ts": "", "lib/v.js": ""}, TYPESCRIPT)
    assert modules.resolve("@u/u", "src/main.ts") == (
        "unresolved",
        "`@u/u` names src/u.js, a JavaScript file, whose calls ripple does not follow from TypeScript",
    )
    assert modules.resolve("lib/v", "src/main.ts")[0] == "unresolved"  # through baseUrl
    assert modules.resolve("@u/t", "src/main.ts") == ("repo", "src/t")  # a TypeScript file is as it was
    assert modules.resolve("@u/nothing", "src/main.ts") == ("package", "@u/nothing")


def test_a_tsconfig_governs_a_javascript_file_only_when_it_allows_javascript(make_repo):
    paths = {"baseUrl": ".", "paths": {"@a/*": ["src/*"]}}
    files = {"src/b.js": "", "src/main.js": ""}
    for options, governed in [
        (paths, False),
        ({**paths, "allowJs": True}, True),
        ({**paths, "checkJs": True}, True),
        ({**paths, "allowJs": False}, False),
    ]:
        modules, _ = mapper(make_repo, {**files, "tsconfig.json": json.dumps({"compilerOptions": options})})
        assert modules.resolve("@a/b", "src/main.js") == (("repo", "src/b") if governed else ("package", "@a/b")), options


def test_allowjs_is_inherited_through_extends(make_repo):
    files = {
        "base.json": json.dumps({"compilerOptions": {"allowJs": True, "baseUrl": ".", "paths": {"@a/*": ["src/*"]}}}),
        "tsconfig.json": json.dumps({"extends": "./base"}),
        "src/b.js": "",
        "src/main.js": "",
    }
    modules, _ = mapper(make_repo, files)
    assert modules.resolve("@a/b", "src/main.js") == ("repo", "src/b")


def test_a_jsconfig_is_read_like_a_tsconfig_that_allows_javascript(make_repo):
    config = {"compilerOptions": {"baseUrl": ".", "paths": {"@a/*": ["src/*"]}, "jsxFactory": "h"}}
    modules, _ = mapper(make_repo, {"jsconfig.json": json.dumps(config), "src/b.js": "", "src/main.js": ""})
    assert modules.resolve("@a/b", "src/main.js") == ("repo", "src/b")
    assert "jsconfig.json sets jsxFactory h" in modules.config.jsx_runtime
    assert modules.config.path == "jsconfig.json" and set(modules.configs) == {""}


def test_a_jsconfig_is_the_project_of_every_file_the_tsconfig_beside_it_does_not_hold(make_repo):
    """tsserver tries the tsconfig.json and then the jsconfig.json of each directory, whatever language the file is in:
    a jsconfig.json alone governs TypeScript files too, and beside a tsconfig.json it governs the files that one leaves out."""
    js_paths = json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@a/*": ["src/*"]}}})
    only_js = {"jsconfig.json": js_paths, "src/b.ts": "", "src/main.ts": ""}
    modules, _ = mapper(make_repo, only_js, TYPESCRIPT)
    assert modules.resolve("@a/b", "src/main.ts") == ("repo", "src/b")
    both = {"jsconfig.json": js_paths, "tsconfig.json": "{}", "src/j.js": "", "src/run.js": "", "src/c.ts": "", "src/entry.ts": ""}
    modules, _ = mapper(make_repo, both)
    assert modules.config.path == "tsconfig.json" and modules.alongside[""].path == "jsconfig.json"
    assert modules.governor("src/run.js").path == "jsconfig.json"  # the tsconfig.json has no allowJs, so it holds no .js file
    assert modules.resolve("@a/j", "src/run.js") == ("repo", "src/j")
    ts, _ = mapper(make_repo, both, TYPESCRIPT)
    assert ts.governor("src/entry.ts").path == "tsconfig.json"  # it holds a .ts file: the jsconfig.json beside it is not consulted
    assert ts.resolve("@a/c", "src/entry.ts") == ("package", "@a/c")


def test_a_tsconfig_that_allows_javascript_holds_it_and_its_jsconfig_is_not_consulted(make_repo):
    both = {
        "tsconfig.json": json.dumps({"compilerOptions": {"allowJs": True}}),
        "jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@a/*": ["src/*"]}}}),
        "src/b.js": "",
        "src/main.js": "",
    }
    modules, _ = mapper(make_repo, both)
    assert modules.governor("src/main.js").path == "tsconfig.json"
    assert modules.resolve("@a/b", "src/main.js") == ("package", "@a/b")


def test_a_jsconfig_beside_a_tsconfig_sets_the_jsx_runtime_of_the_files_it_governs(make_repo):
    files = {
        "tsconfig.json": "{}",
        "jsconfig.json": json.dumps({"compilerOptions": {"jsxFactory": "h"}}),
        "a.jsx": "",
        "b.tsx": "",
    }
    modules, _ = mapper(make_repo, files)
    assert "jsconfig.json sets jsxFactory h" in modules.jsx_runtime("a.jsx")
    assert modules.jsx_runtime("b.tsx") == ""  # the tsconfig.json holds it, and says nothing


def test_a_file_a_wildcard_include_drops_for_a_same_named_file_of_higher_priority_is_in_no_project(make_repo):
    """m.js beside m.ts: the compiler holds m.ts only, so m.js has no aliases (a file `files` names is always held)."""
    config = {"allowJs": True, "baseUrl": ".", "paths": {"@a/*": ["lib/*"]}}
    files = {"lib/t.js": "", "m.ts": "", "m.js": "", "p.js": "", "p.jsx": "", "q.js": ""}
    modules, _ = mapper(make_repo, {**files, "tsconfig.json": json.dumps({"compilerOptions": config})})
    assert [modules.governor(f) is None for f in ("m.js", "p.jsx")] == [True, True]  # outranked by m.ts, by p.js
    assert [modules.governor(f) is not None for f in ("p.js", "q.js")] == [True, True]
    listed, _ = mapper(make_repo, {**files, "tsconfig.json": json.dumps({"compilerOptions": config, "files": ["m.ts", "m.js"]})})
    assert listed.governor("m.js") is not None


def test_a_relative_import_that_names_no_file_under_root_dirs_is_unresolved_and_a_slash_names_a_directory(make_repo):
    modules, _ = mapper(
        make_repo,
        {"tsconfig.json": json.dumps({"compilerOptions": {"rootDirs": ["src", "gen"]}}), "src/a.ts": "", "x.ts": "", "x/index.ts": "", "gen/g.ts": ""},
        TYPESCRIPT,
    )
    assert modules.resolve("./g", "src/a.ts")[0] == "unresolved" and "rootDirs" in modules.resolve("./g", "src/a.ts")[1]
    assert modules.resolve("../x", "src/a.ts") == ("repo", "x")
    assert modules.resolve("../x/", "src/a.ts") == ("repo", "x/index")


def test_an_import_of_a_declaration_file_by_name_is_unresolved_and_never_a_package(make_repo):
    modules, _ = mapper(make_repo, ["src/main.ts", "src/types.d.ts", "src/styles.css"], TYPESCRIPT)
    kind, why = modules.resolve("./types.d.ts", "src/main.ts")
    assert kind == "unresolved" and "a declaration file" in why
    assert modules.resolve("./styles.css", "src/main.ts") == ("package", "./styles.css")


def test_a_nested_tsconfig_without_allowjs_hands_a_javascript_file_to_the_config_above(make_repo):
    files = {
        "jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}),
        "legacy/tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@x/*": ["x/*"]}}}),
        "legacy/x/thing.js": "",
        "legacy/use.js": "",
    }
    modules, _ = mapper(make_repo, files)
    assert modules.governor("legacy/use.js") is modules.configs[""]
    assert modules.resolve("@x/thing", "legacy/use.js") == ("package", "@x/thing")


def test_the_dual_format_layout_folds_to_one_id_and_is_not_a_failure_to_read(make_repo):
    modules, _ = mapper(make_repo, ["lib/x.js", "lib/x.mjs", "lib/x.cjs", "lib/y.js"])
    assert modules.modules == {"lib/x.js": "lib/x", "lib/x.mjs": "lib/x.mjs", "lib/x.cjs": "lib/x.cjs", "lib/y.js": "lib/y"}
    assert sorted(modules.dual_format) == ["lib/x.cjs", "lib/x.mjs"]
    assert modules.resolve("./x.mjs", "lib/y.js") == ("repo", "lib/x.mjs")
    assert modules.resolve("./x.js", "lib/y.js") == ("repo", "lib/x")
    assert modules.resolve("./x.cjs", "lib/y.js") == ("repo", "lib/x.cjs")


def test_an_extensionless_import_with_an_mjs_twin_is_unresolved_because_the_resolvers_disagree(make_repo):
    """The compiler and Node's require read `./dual` as dual.js, Vite and Rollup as dual.mjs: which file runs is a bundler
    setting ripple cannot see, so it is not drawn as an edge to either. A .cjs twin changes nothing (no resolver adds .cjs)."""
    modules, _ = mapper(make_repo, ["lib/use.js", "lib/dual.js", "lib/dual.mjs", "lib/cj.js", "lib/cj.cjs", "lib/dir.mjs", "lib/dir/index.js"])
    kind, why = modules.resolve("./dual", "lib/use.js")
    assert kind == "unresolved" and "lib/dual.js to the compiler and lib/dual.mjs to Vite and Rollup" in why
    assert modules.resolve("./cj", "lib/use.js") == ("repo", "lib/cj")
    assert modules.resolve("./dir", "lib/use.js")[0] == "unresolved"  # x.mjs beside x/index.js


def test_a_clash_that_is_not_a_dual_format_pair_stays_a_failure_to_read(make_repo):
    jsx, _ = mapper(make_repo, ["a.js", "a.jsx", "b/index.js", "b.js"])
    assert sorted(jsx.shadowed) == ["a.jsx", "b/index.js"] and jsx.dual_format == []
    ts, _ = mapper(make_repo, ["a.ts", "a.tsx"], TYPESCRIPT)
    assert ts.shadowed == ["a.tsx"] and ts.dual_format == []


def test_the_configs_a_javascript_index_depends_on_are_the_jsconfig_and_its_extends(make_repo):
    repo = make_repo(
        {
            "jsconfig.json": json.dumps({"extends": "./base.json"}),
            "base.json": "{}",
            "sub/jsconfig.json": "{}",
            "sub/a.js": "",
        }
    )
    assert config_files(repo, find_source_files(repo, JAVASCRIPT)) == ["jsconfig.json", "base.json", "sub/jsconfig.json"]
    assert {"jsconfig.json", "base.json", "sub/jsconfig.json", "sub/a.js"} <= set(fingerprint(repo, JAVASCRIPT))


def test_a_typescript_index_depends_on_which_javascript_files_exist(make_repo):
    """Whether `./x.js` is unresolved or a missing file is the answer of the file being there, so the cache must notice it."""
    repo = make_repo({"src/main.ts": "", "src/x.js": "", "tsconfig.json": "{}"})
    ts = set(fingerprint(repo, TYPESCRIPT))
    assert ts == {"src/main.ts", "tsconfig.json", "src/x.js"}
    (repo / "src" / "x.js").unlink()
    assert set(fingerprint(repo, TYPESCRIPT)) == {"src/main.ts", "tsconfig.json"}


def test_a_tsconfig_that_does_not_allow_javascript_governs_none_of_its_javascript(make_repo):
    modules, _ = mapper(make_repo, {"tsconfig.json": "{}", "a.js": ""})
    assert load_tsconfig(modules.repo).allow_js is False
    assert modules.governor("a.js") is None


def test_the_typescript_fingerprint_records_which_javascript_files_exist_and_not_what_is_in_them(make_repo):
    """Only the other language's files being there changes an answer (unresolved or missing), never their content, so editing
    one must not rebuild this language's graph and adding or deleting one must."""
    repo = make_repo({"src/main.ts": "", "src/x.js": "export const a = 1;\n", "tsconfig.json": "{}"})
    before = fingerprint(repo, TYPESCRIPT)
    assert before["src/x.js"] == [0, 0] and before["src/main.ts"] != [0, 0]
    (repo / "src" / "x.js").write_text("export const a = 2; // changed, longer\n")
    assert fingerprint(repo, TYPESCRIPT) == before
    (repo / "src" / "y.js").write_text("")
    assert set(fingerprint(repo, TYPESCRIPT)) == set(before) | {"src/y.js"}
    (repo / "src" / "x.js").unlink()
    assert "src/x.js" not in fingerprint(repo, TYPESCRIPT)
    js = fingerprint(repo, JAVASCRIPT)  # and the reverse: the .ts files are only there to be named
    assert js["src/main.ts"] == [0, 0] and js["src/y.js"] != [0, 0]


def test_a_jsconfig_edit_is_in_both_languages_fingerprints_when_there_is_one(make_repo):
    repo = make_repo({"src/main.ts": "", "src/a.js": "", "jsconfig.json": "{}"})
    assert "jsconfig.json" in fingerprint(repo, TYPESCRIPT) and "jsconfig.json" in fingerprint(repo, JAVASCRIPT)
    ts_before, js_before = fingerprint(repo, TYPESCRIPT), fingerprint(repo, JAVASCRIPT)
    (repo / "jsconfig.json").write_text('{"compilerOptions": {"jsxFactory": "h"}}')
    assert fingerprint(repo, TYPESCRIPT) != ts_before and fingerprint(repo, JAVASCRIPT) != js_before


def test_an_import_into_a_directory_the_walk_skips_is_a_package_not_a_file_of_the_other_language(make_repo):
    """`../dist/x.js` is no source file to either index (the root dist is skipped), so it is as it was: outside the repo."""
    modules, _ = mapper(make_repo, ["src/main.ts", "dist/x.js", "node_modules/p/y.js", "src/x.js"], TYPESCRIPT)
    assert modules.resolve("../dist/x.js", "src/main.ts") == ("package", "../dist/x.js")
    assert modules.resolve("../node_modules/p/y.js", "src/main.ts") == ("package", "../node_modules/p/y.js")
    assert modules.resolve("./x.js", "src/main.ts")[0] == "unresolved"  # a JavaScript file the other index reads


def test_the_other_languages_file_is_found_by_the_walk_so_a_file_it_skips_is_no_candidate(make_repo):
    modules, _ = mapper(make_repo, ["src/main.js", "src/x.ts", "src/y.d.ts", "build/z.ts"], JAVASCRIPT)
    assert modules.resolve("./x", "src/main.js")[0] == "unresolved"
    assert modules.resolve("./y", "src/main.js") == ("missing", "./y")  # a declaration file is read by neither
    assert modules.resolve("../build/z", "src/main.js") == ("missing", "../build/z")  # build/z.ts is in a directory the walk skips


# ---- round 2: what a package.json, a directory and a config that does not hold a file decide


def test_a_directory_of_the_repo_is_unresolved_never_a_package_or_missing(make_repo):
    files = {"main.js": "", "lib/a.js": "", "assets/logo.svg": "<svg/>", "idx/index.js": "", "idx/package.json": "{}", "ok/index.js": ""}
    modules, _ = mapper(make_repo, files)
    kind, why = modules.resolve("./lib", "main.js")
    assert kind == "unresolved" and "the directory lib/" in why  # no index file
    assert modules.resolve("./lib/", "main.js")[0] == "unresolved" and modules.resolve("./assets", "main.js")[0] == "unresolved"
    assert modules.resolve("./idx", "main.js")[0] == "unresolved"  # an index.js, and a package.json that may name another file
    assert modules.resolve("./idx/index.js", "main.js") == ("repo", "idx")  # named in full it is a file
    assert modules.resolve("./ok", "main.js") == ("repo", "ok")  # a directory with an index file and no package.json
    assert modules.resolve("./assets/logo.svg", "main.js") == ("package", "./assets/logo.svg")  # a file that is not source
    assert modules.resolve("./nothing", "main.js") == ("missing", "./nothing")


def test_the_root_directory_with_a_package_json_is_unresolved_and_without_one_is_its_index(make_repo):
    with_package, _ = mapper(make_repo, {"index.js": "", "test/t.js": "", "package.json": '{"main": "lib/x.js"}'})
    assert with_package.resolve("..", "test/t.js")[0] == "unresolved" and with_package.resolve("../index.js", "test/t.js") == ("repo", "index")


def test_an_alias_target_that_is_a_directory_or_comes_after_one_is_unresolved(make_repo):
    config = {"compilerOptions": {"allowJs": True, "baseUrl": ".", "paths": {"@x/*": ["a/*", "b/*"]}}}
    modules, _ = mapper(make_repo, {"tsconfig.json": json.dumps(config), "a/foo/package.json": '{"main": "bar.js"}', "a/foo/bar.js": "", "b/foo.js": "", "b/baz.js": "", "main.js": ""})
    assert modules.resolve("@x/foo", "main.js")[0] == "unresolved"  # the compiler enters a/foo first: its package.json picks the file
    assert modules.resolve("@x/baz", "main.js") == ("repo", "b/baz")  # a/baz is nothing, so b/baz.js


def test_a_hash_import_and_a_package_name_of_the_repo_are_unresolved_and_other_bare_names_stay_packages(make_repo):
    files = {
        "package.json": json.dumps({"name": "mypkg"}), "main.js": "",
        "packages/util/package.json": json.dumps({"name": "@me/util"}), "packages/util/src/index.js": "",
        "vendor/package.json": json.dumps({"name": "vendored"}),  # no source file in it or below it: not looked at
    }
    modules, _ = mapper(make_repo, files)
    assert modules.resolve("#lib/x.js", "main.js")[0] == "unresolved" and "subpath import" in modules.resolve("#lib/x.js", "main.js")[1]
    for specifier in ("mypkg", "mypkg/sub", "@me/util", "@me/util/src/index.js"):
        kind, why = modules.resolve(specifier, "main.js")
        assert kind == "unresolved" and "names the package" in why, specifier
    for specifier in ("react", "lodash/fp", "@me/other", "mypkg-two", "vendored", "@types/node", "node:fs"):
        assert modules.resolve(specifier, "main.js") == ("package", specifier), specifier


def test_a_typescript_file_is_governed_by_the_tsconfig_that_holds_it_and_not_by_a_nearer_jsconfig(make_repo):
    config = {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}, "allowJs": True}, "include": ["src"]}
    files = {"tsconfig.json": json.dumps(config), "src/legacy/jsconfig.json": "{}", "src/util.ts": "", "src/legacy/old.ts": "", "src/legacy/older.js": ""}
    ts, repo = mapper(make_repo, files, TYPESCRIPT)
    js = ModuleMap(repo, find_source_files(repo, JAVASCRIPT))
    assert ts.governor("src/legacy/old.ts").path == "tsconfig.json" and ts.resolve("@/util", "src/legacy/old.ts") == ("repo", "src/util")
    assert js.governor("src/legacy/older.js").path == "src/legacy/jsconfig.json"  # tsserver's project for a JavaScript file
    assert js.resolve("@/util", "src/legacy/older.js") == ("package", "@/util")


def test_a_typescript_file_no_tsconfig_holds_goes_to_the_jsconfig_that_does(make_repo):
    config = {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}, "include": ["src"]}
    modules, _ = mapper(make_repo, {"jsconfig.json": json.dumps(config), "src/util.ts": "", "src/a.ts": "", "scripts/b.ts": ""}, TYPESCRIPT)
    assert modules.resolve("@/util", "src/a.ts") == ("repo", "src/util")
    assert modules.resolve("@/util", "scripts/b.ts") == ("package", "@/util")  # its include leaves scripts out


def test_a_file_a_twin_keeps_out_of_an_include_has_unknown_aliases_and_a_file_no_include_names_has_none(make_repo):
    config = {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}, "include": ["src"]}
    modules, _ = mapper(make_repo, {"tsconfig.json": json.dumps(config), "src/lib.ts": "", "src/x.ts": "", "src/x.tsx": "", "scripts/s.ts": ""}, TYPESCRIPT)
    assert modules.resolve("@/lib", "src/x.ts") == ("repo", "src/lib")
    kind, why = modules.resolve("@/lib", "src/x.tsx")
    assert kind == "unresolved" and "same-named file of higher priority" in why
    assert modules.resolve("@/lib", "scripts/s.ts") == ("package", "@/lib")
    assert modules.resolve("react", "src/x.tsx") == ("package", "react")  # no alias of that config is the name


def test_config_dir_in_a_base_config_is_the_directory_of_the_config_that_extends_it(make_repo):
    base = {
        "compilerOptions": {"baseUrl": "${configDir}", "paths": {"@/*": ["${configDir}/lib/*"]}, "rootDir": "${configDir}/src", "outDir": "${configDir}/out"},
        "include": ["${configDir}/src", "${configDir}/lib"],
        "exclude": ["${configDir}/src/skip"],
    }
    repo = make_repo({"base.json": json.dumps(base), "pkg/tsconfig.json": json.dumps({"extends": "../base.json"}), "pkg/src/a.ts": ""})
    config = load_tsconfig(repo, "pkg/tsconfig.json")
    assert config.base_url == "pkg" and config.paths == {"@/*": ["pkg/lib/*"]}
    assert config.include == ("pkg/src", "pkg/lib") and config.exclude == ("pkg/src/skip",)
    assert config.governs("pkg/src/a.ts") and not config.governs("pkg/src/skip/b.ts") and not config.governs("lib/u.ts")


def test_a_package_json_and_a_referenced_tsconfig_build_json_are_in_both_fingerprints_and_an_edit_to_either_moves_them(make_repo):
    files = {
        "tsconfig.json": json.dumps({"files": [], "references": [{"path": "./tsconfig.build.json"}]}),
        "tsconfig.build.json": "{}", "package.json": json.dumps({"name": "p"}), "src/a.ts": "", "src/b.js": "",
    }
    repo = make_repo(files)
    for profile in (TYPESCRIPT, JAVASCRIPT):
        assert {"package.json", "tsconfig.build.json"} <= set(fingerprint(repo, profile))
    for name, text in (("package.json", json.dumps({"name": "another"})), ("tsconfig.build.json", '{"compilerOptions": {"outDir": "out"}}')):
        before = {profile.name: fingerprint(repo, profile) for profile in (TYPESCRIPT, JAVASCRIPT)}
        (repo / name).write_text(text)
        assert all(fingerprint(repo, profile) != before[profile.name] for profile in (TYPESCRIPT, JAVASCRIPT)), name


def test_an_accessor_is_found_through_comments_between_its_tokens():
    from ripple.ts_indexer import _accessor_names

    source = b"class A { get /* x */ total /* y */ () { return 1; } static set\n // z\n other /* w */ <T>(v: T) {} get plain() {} }"
    assert _accessor_names(source) == {"total", "other", "plain"}
