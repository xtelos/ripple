"""A tsconfig's `paths` and `baseUrl` apply only to the files that tsconfig governs.

A file belongs to the nearest tsconfig.json up its directory tree whose `files`, `include` and `exclude` cover it
(TypeScript's defaults: every source file under its directory when `include` is absent, minus `exclude`,
node_modules and `outDir`). A file no config governs gets no alias, so an import that needed one is not guessed.
The resolver part needs no parser; the edge tests at the bottom need tree-sitter and are skipped without it.
"""

import json

import pytest

from ripple.cache import CACHE_FORMAT, fingerprint
from ripple.indexer import find_source_files
from ripple.profile import TYPESCRIPT
from ripple.ts_modules import ModuleMap, load_tsconfig

ROOT_ALIAS = {"compilerOptions": {"baseUrl": ".", "paths": {"pkg": ["src/index.ts"]}}}


def modules_of(make_repo, files):
    repo = make_repo({k: v if isinstance(v, str) else json.dumps(v) for k, v in files.items()})
    return ModuleMap(repo, find_source_files(repo, TYPESCRIPT)), repo


# ---- (a) a root config that does not cover an app does not give the app its aliases


def test_a_file_the_root_config_does_not_include_gets_no_alias_and_a_file_it_includes_does(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {**ROOT_ALIAS, "include": ["src"]},
            "src/index.ts": "export function X() {}\n",
            "src/use.ts": "",
            "apps/app/package.json": {"dependencies": {"pkg": "9.0.0"}},
            "apps/app/main.ts": "",
        },
    )
    assert modules.resolve("pkg", "src/use.ts") == ("repo", "src")
    assert modules.resolve("pkg", "apps/app/main.ts") == ("package", "pkg")


def test_baseurl_alone_is_scoped_the_same_way(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"compilerOptions": {"baseUrl": "."}, "include": ["src"]},
            "src/lib/z.ts": "",
            "src/a.ts": "",
            "other/b.ts": "",
        },
    )
    assert modules.resolve("src/lib/z", "src/a.ts") == ("repo", "src/lib/z")
    assert modules.resolve("src/lib/z", "other/b.ts") == ("package", "src/lib/z")


def test_a_relative_import_is_resolved_for_a_file_no_config_governs(make_repo):
    modules, _ = modules_of(
        make_repo,
        {"tsconfig.json": {**ROOT_ALIAS, "include": ["src"]}, "src/index.ts": "", "apps/app/main.ts": "", "apps/app/util.ts": ""},
    )
    assert modules.resolve("./util", "apps/app/main.ts") == ("repo", "apps/app/util")
    assert modules.resolve("../../../src", "apps/app/main.ts") == ("package", "../../../src")  # outside the repo


# ---- (b) an app's own config decides, not the root's


def test_an_app_with_its_own_config_resolves_by_its_own_paths_and_not_the_roots(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"compilerOptions": {"paths": {"pkg": ["src/index.ts"], "rootonly": ["src/only.ts"]}}},
            "src/index.ts": "",
            "src/only.ts": "",
            "src/use.ts": "",
            "apps/app/tsconfig.json": {"compilerOptions": {"paths": {"pkg": ["./lib/pkg.ts"], "@app/*": ["./src/*"]}}, "include": ["src"]},
            "apps/app/lib/pkg.ts": "",
            "apps/app/src/main.ts": "",
            "apps/app/src/x.ts": "",
        },
    )
    here = "apps/app/src/main.ts"
    assert modules.resolve("pkg", here) == ("repo", "apps/app/lib/pkg")  # the app's pkg, not src/index.ts
    assert modules.resolve("@app/x", here) == ("repo", "apps/app/src/x")
    assert modules.resolve("rootonly", here) == ("package", "rootonly")  # the root's alias is not the app's
    assert modules.resolve("pkg", "src/use.ts") == ("repo", "src")  # the root still governs its own files
    assert modules.resolve("@app/x", "src/use.ts") == ("package", "@app/x")


def test_an_app_config_that_does_not_cover_a_file_leaves_it_to_the_next_config_up(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"compilerOptions": {"paths": {"pkg": ["src/index.ts"]}}},
            "src/index.ts": "",
            "apps/app/tsconfig.json": {"compilerOptions": {"paths": {"@app/*": ["./src/*"]}}, "include": ["src"]},
            "apps/app/src/x.ts": "",
            "apps/app/scripts/build.ts": "",
        },
    )
    # scripts/ is outside the app's include and inside the root's default one
    assert modules.resolve("pkg", "apps/app/scripts/build.ts") == ("repo", "src")
    assert modules.resolve("@app/x", "apps/app/scripts/build.ts") == ("package", "@app/x")


# ---- (c) no include: every source file under the config's directory, in nested directories too


def test_with_no_include_every_file_under_the_config_is_governed(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": ROOT_ALIAS,
            "src/index.ts": "",
            "a.ts": "",
            "deep/er/still/z.tsx": "",
            "packages/p/src/q.mts": "",
        },
    )
    for importer in ("a.ts", "deep/er/still/z.tsx", "packages/p/src/q.mts"):
        assert modules.resolve("pkg", importer) == ("repo", "src")


def test_an_app_config_that_names_no_files_governs_nothing_so_the_root_default_covers_the_app(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": ROOT_ALIAS,
            "src/index.ts": "",
            "apps/app/tsconfig.json": {"files": [], "references": [{"path": "./tsconfig.app.json"}]},
            "apps/app/src/main.ts": "",
        },
    )
    # a reference is not followed: the app config covers no file itself, and the root's default include does
    assert modules.resolve("pkg", "apps/app/src/main.ts") == ("repo", "src")


def test_with_a_files_list_and_no_include_only_the_listed_files_are_governed(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {**ROOT_ALIAS, "files": ["main.ts", "./lib/../entry.ts"]},
            "src/index.ts": "",
            "main.ts": "",
            "entry.ts": "",
            "other.ts": "",
        },
    )
    assert modules.resolve("pkg", "main.ts") == ("repo", "src")
    assert modules.resolve("pkg", "entry.ts") == ("repo", "src")
    assert modules.resolve("pkg", "other.ts") == ("package", "pkg")


# ---- (d) exclude


def test_an_excluded_directory_is_not_governed(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {**ROOT_ALIAS, "exclude": ["legacy", "**/*.spec.ts", "gen/*.ts"]},
            "src/index.ts": "",
            "src/a.ts": "",
            "src/a.spec.ts": "",
            "legacy/old.ts": "",
            "legacy/deeper/older.ts": "",
            "gen/g.ts": "",
            "gen/sub/h.ts": "",
        },
    )
    assert modules.resolve("pkg", "src/a.ts") == ("repo", "src")
    assert modules.resolve("pkg", "legacy/old.ts") == ("package", "pkg")
    assert modules.resolve("pkg", "legacy/deeper/older.ts") == ("package", "pkg")
    assert modules.resolve("pkg", "src/a.spec.ts") == ("package", "pkg")
    assert modules.resolve("pkg", "gen/g.ts") == ("package", "pkg")
    assert modules.resolve("pkg", "gen/sub/h.ts") == ("repo", "src")  # gen/*.ts is one level


def test_an_exclude_does_not_remove_a_file_the_files_list_names(make_repo):
    modules, _ = modules_of(
        make_repo,
        {"tsconfig.json": {**ROOT_ALIAS, "files": ["legacy/old.ts"], "exclude": ["legacy"]}, "src/index.ts": "", "legacy/old.ts": ""},
    )
    assert modules.resolve("pkg", "legacy/old.ts") == ("repo", "src")


def test_the_out_dir_is_excluded_when_exclude_is_not_written_and_not_otherwise(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"compilerOptions": {**ROOT_ALIAS["compilerOptions"], "outDir": "build"}},
            "src/index.ts": "",
            "build/out.ts": "",
        },
    )
    assert modules.resolve("pkg", "build/out.ts") == ("package", "pkg")
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"compilerOptions": {**ROOT_ALIAS["compilerOptions"], "outDir": "build"}, "exclude": ["other"]},
            "src/index.ts": "",
            "build/out.ts": "",
        },
    )
    assert modules.resolve("pkg", "build/out.ts") == ("repo", "src")


# ---- (e) extends


def test_paths_and_coverage_come_down_an_extends_chain(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.base.json": {"compilerOptions": {"baseUrl": ".", "paths": {"pkg": ["src/index.ts"]}}},
            "tsconfig.json": {"extends": "./tsconfig.base.json", "include": ["src"]},
            "src/index.ts": "",
            "src/use.ts": "",
            "apps/app/tsconfig.json": {"extends": "../../tsconfig.base", "include": ["src"]},
            "apps/app/src/main.ts": "",
            "apps/app/other.ts": "",
            "elsewhere/e.ts": "",
        },
    )
    assert modules.resolve("pkg", "src/use.ts") == ("repo", "src")
    # the app's config inherits the paths and the base's baseUrl (the repo root), and governs apps/app/src
    assert modules.resolve("pkg", "apps/app/src/main.ts") == ("repo", "src")
    assert modules.resolve("pkg", "elsewhere/e.ts") == ("package", "pkg")  # root include is src only


def test_an_include_written_in_a_base_config_is_relative_to_that_config_and_the_child_may_override_it(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "config/base.json": {"compilerOptions": {"baseUrl": ".."}, "include": ["../src"], "exclude": ["../src/skip"]},
            "tsconfig.json": {"extends": "./config/base", "compilerOptions": {"paths": {"pkg": ["src/index.ts"]}}},
            "src/index.ts": "",
            "src/use.ts": "",
            "src/skip/s.ts": "",
            "lib/l.ts": "",
        },
    )
    assert modules.resolve("pkg", "src/use.ts") == ("repo", "src")
    assert modules.resolve("pkg", "src/skip/s.ts") == ("package", "pkg")
    assert modules.resolve("pkg", "lib/l.ts") == ("package", "pkg")
    modules, _ = modules_of(
        make_repo,
        {
            "config/base.json": {"compilerOptions": {"baseUrl": ".."}, "include": ["../src"]},
            "tsconfig.json": {"extends": "./config/base", "compilerOptions": {"paths": {"pkg": ["src/index.ts"]}}, "include": ["lib"]},
            "src/index.ts": "",
            "lib/l.ts": "",
        },
    )
    assert modules.resolve("pkg", "lib/l.ts") == ("repo", "src")  # the child's include replaces the base's
    assert modules.resolve("pkg", "src/index.ts") == ("package", "pkg")


# ---- the include matcher itself


@pytest.mark.parametrize(
    ("include", "covered", "not_covered"),
    [
        (["src"], ["src/a.ts", "src/x/y/b.tsx"], ["srcx/a.ts", "a.ts", "lib/src/a.ts"]),
        (["src/"], ["src/a.ts"], ["a.ts"]),
        (["./src"], ["src/a.ts"], ["a.ts"]),
        (["src/*"], ["src/a.ts"], ["a.ts", "src/x/b.ts"]),  # a wildcard last part is one level, not a directory
        (["src/*.ts"], ["src/a.ts"], ["src/x/b.ts", "src/c.tsx", "src/.hidden.ts"]),  # a leading * does not match a dot
        (["src/**/*.ts"], ["src/a.ts", "src/x/y/b.ts"], ["a.ts", "src/c.tsx"]),
        (["**/*.test.*"], ["a.test.ts", "x/y/b.test.tsx"], ["a.ts", "a.testx.ts"]),
        (["src/a?.ts"], ["src/ab.ts"], ["src/a.ts", "src/abc.ts"]),
        (["main.ts"], ["main.ts"], ["x/main.ts", "main.tsx"]),
        (["**"], [], ["a.ts", "src/a.ts"]),  # a trailing ** in an include names nothing, as in the compiler
        (["**/*"], ["a.ts", "x/y/z.ts"], [".hidden/a.ts", "x/.cache/a.ts", "x/node_modules/p/a.ts"]),
        (["lib", "test"], ["lib/a.ts", "test/b.ts"], ["src/a.ts"]),
        ([], [], ["a.ts"]),
    ],
)
def test_an_include_covers_what_the_compiler_says_it_covers(make_repo, include, covered, not_covered):
    files = {"tsconfig.json": {**ROOT_ALIAS, "include": include}, "src/index.ts": ""}
    for rel in [*covered, *not_covered]:
        files.setdefault(rel, "")
    modules, _ = modules_of(make_repo, files)
    for rel in covered:
        assert modules.resolve("pkg", rel) == ("repo", "src"), rel
    for rel in not_covered:
        if rel != "src/index.ts":
            assert modules.resolve("pkg", rel) == ("package", "pkg"), rel


@pytest.mark.parametrize(
    ("config", "covered", "not_covered"),
    [
        # an exclude's ** is any directories at all, hidden ones too (the compiler's exclude matcher)
        (
            {"include": [".storybook/**/*", "src"], "exclude": ["**/*.stories.tsx"]},
            [".storybook/main.ts", "src/a.ts"],
            [".storybook/a.stories.tsx", "src/x.stories.tsx", "src/.hid/b.stories.tsx"],
        ),
        ({"include": [".hid/**/*"], "exclude": ["**/x.ts"]}, [".hid/y.ts"], [".hid/x.ts", ".hid/deep/x.ts"]),
        ({"include": ["src"], "exclude": ["src/**/.cache"]}, ["src/a.ts"], ["src/.cache/a.ts", "src/x/.cache/b.ts"]),
        # an include's ** and the default include (**/*) never reach a hidden directory
        ({}, ["a.ts", "src/b.ts"], [".storybook/main.ts", "src/.hid/c.ts", "src/node_modules/p/i.ts"]),
        ({"include": ["**/*.ts"]}, ["src/b.ts"], [".storybook/main.ts", "src/.hid/c.ts"]),
        ({"include": ["src/**/*"], "exclude": ["lib"]}, ["src/b.ts"], ["src/.hid/c.ts"]),
        # a * in an exclude matches a leading dot, which an include's * does not
        ({"include": ["src"], "exclude": ["src/*.ts"]}, ["src/x/a.ts"], ["src/a.ts", "src/.a.ts"]),
    ],
)
def test_an_exclude_reaches_hidden_directories_an_include_does_not(make_repo, config, covered, not_covered):
    loaded = load_tsconfig(make_repo({"tsconfig.json": json.dumps(config)}))
    for rel in covered:
        assert loaded.governs(rel), rel
    for rel in not_covered:
        assert not loaded.governs(rel), rel


@pytest.mark.parametrize(
    ("config", "covered", "not_covered"),
    [
        ({"include": ["src\\**\\*"]}, ["src/x.ts", "src/a/b.ts"], ["lib/y.ts"]),
        ({"include": ["src\\a"]}, ["src/a/b.ts"], ["src/c.ts", "lib/y.ts"]),
        ({"include": ["src"], "exclude": ["src\\skip", "**\\*.test.ts"]}, ["src/x.ts"], ["src/skip/s.ts", "src/x.test.ts"]),
        ({"files": ["src\\main.ts", ".\\other.ts"]}, ["src/main.ts", "other.ts"], ["src/x.ts"]),
        ({"include": ["src"], "compilerOptions": {"outDir": "src\\out"}}, ["src/x.ts"], ["src/out/o.ts"]),  # outDir is left out when exclude is not written
        ({"compilerOptions": {"outDir": "dist\\out"}}, ["src/x.ts"], ["dist/out/o.ts"]),
    ],
)
def test_a_backslash_in_a_config_path_is_a_slash(make_repo, config, covered, not_covered):
    loaded = load_tsconfig(make_repo({"tsconfig.json": json.dumps(config)}))
    for rel in covered:
        assert loaded.governs(rel), rel
    for rel in not_covered:
        assert not loaded.governs(rel), rel


def test_backslashes_in_paths_targets_baseurl_extends_and_references_are_slashes(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"files": [], "references": [{"path": ".\\app\\tsconfig.app.json"}]},
            "app/tsconfig.app.json": {
                "extends": ".\\base.json",
                "compilerOptions": {"baseUrl": ".\\src", "paths": {"@/*": ["lib\\*"], "own": ["..\\shared\\own.ts"]}},
            },
            "app/base.json": {"include": ["src\\lib"]},  # read only through the extends: without it the app config covers all of app
            "app/src/lib/u.ts": "",
            "app/src/lib/v.ts": "",
            "app/src/main.ts": "",
            "app/shared/own.ts": "",
        },
    )
    assert modules.governor("app/src/lib/v.ts").path == "app/tsconfig.app.json"
    assert modules.governor("app/src/main.ts") is None  # the extended include names src/lib only
    assert modules.resolve("@/u", "app/src/lib/v.ts") == ("repo", "app/src/lib/u")  # a paths target, from the baseUrl
    assert modules.resolve("own", "app/src/lib/v.ts") == ("repo", "app/shared/own")
    assert modules.resolve("lib/u", "app/src/lib/v.ts") == ("repo", "app/src/lib/u")  # the baseUrl itself


def test_a_pattern_written_with_backslashes_governs_what_the_same_one_with_slashes_does(make_repo):
    """src\\** and src/** name the same files, and the config governs the same ones whichever is written."""
    for pattern in ("src\\**\\*", "src/**/*"):
        modules, _ = modules_of(make_repo, {"tsconfig.json": {**ROOT_ALIAS, "include": [pattern]}, "src/index.ts": "", "src/use.ts": ""})
        assert modules.resolve("pkg", "src/use.ts") == ("repo", "src"), pattern


def test_a_wildcard_does_not_reach_into_node_modules_but_a_written_path_does_not_matter_there(make_repo):
    modules, _ = modules_of(
        make_repo, {"tsconfig.json": ROOT_ALIAS, "src/index.ts": "", "node_modules/p/i.ts": "", "src/node_modules/q/i.ts": ""}
    )
    assert modules.resolve("pkg", "node_modules/p/i.ts") == ("package", "pkg")
    assert modules.resolve("pkg", "src/node_modules/q/i.ts") == ("package", "pkg")


# ---- configs that cannot be read, and what is listed for the cache


def test_a_file_under_a_config_that_cannot_be_read_is_not_given_the_next_config_up(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": ROOT_ALIAS,
            "src/index.ts": "",
            "apps/app/tsconfig.json": "{ not json",
            "apps/app/main.ts": "",
        },
    )
    # which config governs main.ts is not known, so no alias is applied
    assert modules.resolve("pkg", "apps/app/main.ts") == ("package", "pkg")
    assert "apps/app/tsconfig.json" in modules.problems


def test_a_config_that_extends_one_that_cannot_be_read_still_governs_its_own_files(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"extends": "./base.json", "compilerOptions": {"paths": {"pkg": ["src/index.ts"]}}, "include": ["src"]},
            "base.json": "{ not json",
            "src/index.ts": "",
            "src/use.ts": "",
            "other/o.ts": "",
        },
    )
    assert modules.resolve("pkg", "src/use.ts") == ("repo", "src")  # the root was read; only its base was not
    assert modules.resolve("pkg", "other/o.ts") == ("package", "pkg")
    assert "tsconfig.json" in modules.problems


def test_a_config_covers_source_files_only(make_repo):
    repo = make_repo({"tsconfig.json": json.dumps({"include": ["src"]})})
    config = load_tsconfig(repo)
    assert config.governs("src/a.ts") and config.governs("src/a.tsx") and config.governs("src/x/a.mts")
    assert not config.governs("src/readme.md") and not config.governs("src/data.json")


def test_the_root_config_stays_the_one_load_tsconfig_reads(make_repo):
    repo = make_repo({"tsconfig.json": json.dumps(ROOT_ALIAS), "apps/app/tsconfig.json": json.dumps({"compilerOptions": {"paths": {"z": ["z.ts"]}}})})
    assert load_tsconfig(repo).paths == {"pkg": ["src/index.ts"]}


def test_every_nested_config_is_listed_so_an_edit_to_one_changes_the_fingerprint(make_repo):
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"extends": "./base.json"}),
            "base.json": "{}",
            "apps/app/tsconfig.json": json.dumps({"extends": "../../shared.json"}),
            "shared.json": "{}",
            "apps/app/src/main.ts": "",
            "packages/p/tsconfig.json": "{}",
            "packages/p/index.ts": "",
            "empty/tsconfig.json": "{}",  # no source below it: nothing it could govern
            "node_modules/dep/tsconfig.json": "{}",
            "node_modules/dep/i.ts": "",
        }
    )
    assert set(fingerprint(repo, TYPESCRIPT)) == {
        "tsconfig.json", "base.json", "apps/app/tsconfig.json", "shared.json", "packages/p/tsconfig.json",
        "apps/app/src/main.ts", "packages/p/index.ts",
    }


def test_a_cache_written_before_a_config_governed_only_its_own_files_is_rebuilt(make_repo, tmp_path_factory):
    """Format 11: the aliases a file gets depend on which config governs it. An old graph resolved the root's
    aliases in every file, so it names edges that are no longer stated."""
    _edge_tests()  # the graph is built here, so the parser is needed
    from ripple.cache import cache_file, load_graph
    from ripple import __version__

    repo = make_repo({"m.ts": "export function f() {}\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir, profile=TYPESCRIPT)
    path = cache_file(repo.resolve(), cache_dir, TYPESCRIPT)
    saved = json.loads(path.read_text())
    assert saved["format"] == CACHE_FORMAT != f"{__version__}-10"
    saved["format"] = f"{__version__}-10"
    saved["graph"]["symbols"] = []
    path.write_text(json.dumps(saved))
    graph, cached = load_graph(repo, cache_dir, profile=TYPESCRIPT)
    assert cached is False
    assert "m:f" in graph.symbols


# ---- the edges


def _edge_tests():
    pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
    pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
    from ripple.indexer import build_graph

    return build_graph


def pairs(graph, *kinds):
    return {(e.caller, e.callee, e.kind) for e in graph.edges if e.kind in kinds}


def test_an_app_the_root_config_does_not_cover_gets_no_calls_or_renders_edge_into_the_workspace_package(make_repo):
    build_graph = _edge_tests()
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"paths": {"pkg": ["src/index.tsx"]}}, "include": ["src"]}),
            "src/index.tsx": "export function X() { return <i />; }\nexport function helper() {}\n",
            "src/use.tsx": "import { X, helper } from 'pkg';\nexport function Use() { helper(); return <X />; }\n",
            "apps/app/package.json": json.dumps({"dependencies": {"pkg": "9.0.0"}}),
            "apps/app/main.tsx": "import { X, helper } from 'pkg';\nexport function Calendar() { helper(); return <X />; }\n",
        }
    )
    graph = build_graph(repo, TYPESCRIPT)
    assert pairs(graph, "call", "renders", "renderer") == {
        ("src/use:Use", "src:helper", "call"),
        ("src/use:Use", "src:X", "renders"),
    }
    assert graph.external_calls == 2  # in the app, helper() and <X /> are a call and an element from a package


def test_an_app_with_its_own_config_gets_its_own_alias_edges(make_repo):
    build_graph = _edge_tests()
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"paths": {"pkg": ["src/index.ts"]}}}),
            "src/index.ts": "export function helper() {}\n",
            "apps/app/tsconfig.json": json.dumps({"compilerOptions": {"paths": {"pkg": ["./lib/pkg.ts"]}}, "include": ["src"]}),
            "apps/app/lib/pkg.ts": "export function helper() {}\n",
            "apps/app/src/main.ts": "import { helper } from 'pkg';\nexport function go() { helper(); }\n",
        }
    )
    assert pairs(build_graph(repo, TYPESCRIPT), "call") == {("apps/app/src/main:go", "apps/app/lib/pkg:helper", "call")}


def test_a_nested_config_that_cannot_be_read_is_listed_with_the_files_that_did_not_parse(make_repo):
    build_graph = _edge_tests()
    repo = make_repo({"apps/app/tsconfig.json": "{ not json", "apps/app/a.ts": "export function f() {}\n", "ok.ts": "export function g() {}\n"})
    assert build_graph(repo, TYPESCRIPT).parse_errors == ["apps/app/tsconfig.json"]


# ---- `references`: a solution-style root config hands its files to the projects it lists

APP = {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}, "include": ["src"]}
SOLUTION = {  # the create-vite layout, with the alias also written in the root (shadcn's install does both)
    "files": [],
    "references": [{"path": "./tsconfig.app.json"}, {"path": "./tsconfig.node.json"}],
    "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}},
}
VITE = {
    "tsconfig.json": SOLUTION,
    "tsconfig.app.json": APP,
    "tsconfig.node.json": {"include": ["vite.config.ts"]},
    "vite.config.ts": "export default {};\n",
    "src/App.tsx": "import { cn } from '@/lib/utils';\nexport function App() { return cn('a'); }\n",
    "src/lib/utils.ts": "export function cn(x: string) { return x; }\n",
}


def test_a_file_only_a_referenced_project_covers_gets_that_projects_aliases(make_repo):
    modules, _ = modules_of(make_repo, VITE)
    assert modules.resolve("@/lib/utils", "src/App.tsx") == ("repo", "src/lib/utils")
    assert modules.governor("src/App.tsx").path == "tsconfig.app.json"
    # the node project covers vite.config.ts and writes no paths: nothing it imports is an alias
    assert modules.governor("vite.config.ts").path == "tsconfig.node.json"


def test_the_references_are_found_in_a_config_that_writes_no_paths_of_its_own(make_repo):
    modules, _ = modules_of(make_repo, {**VITE, "tsconfig.json": {"files": [], "references": SOLUTION["references"]}})
    assert modules.resolve("@/lib/utils", "src/App.tsx") == ("repo", "src/lib/utils")


def test_a_referenced_project_that_does_not_cover_the_file_does_not_govern_it(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"files": [], "references": [{"path": "./a"}, {"path": "./b"}]},
            "a/tsconfig.json": {"compilerOptions": {"paths": {"@/*": ["./*"]}}, "include": ["."]},
            "b/tsconfig.json": {"compilerOptions": {"paths": {"@/*": ["./*"]}}, "include": ["."]},
            "a/x.ts": "",
            "b/x.ts": "",
            "c/x.ts": "",
        },
    )
    assert modules.resolve("@/x", "a/x.ts") == ("repo", "a/x")
    assert modules.resolve("@/x", "b/x.ts") == ("repo", "b/x")
    # c is in neither project: ripple does not say which of the two `@/x` would have meant
    kind, why = modules.resolve("@/x", "c/x.ts")
    assert kind == "unresolved" and "a/tsconfig.json" in why


def test_a_reference_names_a_config_file_or_a_directory_and_is_relative_to_the_config_that_lists_it(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"files": [], "references": [{"path": "./packages/web"}, {"path": "./tsconfig.lib.json"}]},
            "tsconfig.lib.json": {"compilerOptions": {"paths": {"lib": ["./lib/index.ts"]}}, "include": ["lib"]},
            "lib/index.ts": "",
            "lib/use.ts": "",
            "packages/web/tsconfig.json": {
                "files": [],
                "references": [{"path": "../shared/tsconfig.shared.json"}],
                "compilerOptions": {"paths": {"web": ["./src/index.ts"]}},
                "include": ["src"],
            },
            "packages/web/src/index.ts": "",
            "packages/web/src/use.ts": "",
            "packages/shared/tsconfig.shared.json": {"compilerOptions": {"paths": {"shared": ["./s.ts"]}}, "include": ["."]},
            "packages/shared/s.ts": "",
            "packages/shared/use.ts": "",
        },
    )
    assert modules.resolve("lib", "lib/use.ts") == ("repo", "lib")  # a file path
    assert modules.resolve("web", "packages/web/src/use.ts") == ("repo", "packages/web/src")  # a directory
    assert modules.resolve("shared", "packages/shared/use.ts") == ("repo", "packages/shared/s")  # one level further, `..` from the lister


def test_a_reference_that_names_nothing_is_ignored(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            **VITE,
            "tsconfig.json": {
                "files": [],
                "references": [{"path": "./nope"}, {"path": "./nope.json"}, {"path": "../outside"}, {"path": 3}, "./tsconfig.node.json", {}, {"path": "./tsconfig.app.json"}],
            },
        },
    )
    assert modules.resolve("@/lib/utils", "src/App.tsx") == ("repo", "src/lib/utils")
    assert modules.problems == {}  # absent configs are not read, so none could fail to be


def test_a_reference_that_cannot_be_read_is_listed_and_governs_nothing_with_an_alias(make_repo):
    modules, _ = modules_of(
        make_repo,
        {**VITE, "tsconfig.json": {"files": [], "references": [{"path": "./tsconfig.app.json"}]}, "tsconfig.app.json": "{ not json"},
    )
    assert "tsconfig.app.json" in modules.problems
    assert modules.resolve("@/lib/utils", "src/App.tsx")[0] != "repo"


def test_references_that_name_each_other_or_themselves_are_read_once(make_repo):
    modules, repo = modules_of(
        make_repo,
        {
            "tsconfig.json": {"files": [], "references": [{"path": "./a.json"}, {"path": "./tsconfig.json"}]},
            "a.json": {"files": [], "references": [{"path": "./tsconfig.json"}, {"path": "./a.json"}, {"path": "./b.json"}]},
            "b.json": {"references": [{"path": "./a.json"}], "compilerOptions": {"paths": {"@/*": ["./src/*"]}}, "include": ["src"]},
            "src/x.ts": "",
            "src/y.ts": "",
        },
    )
    assert modules.resolve("@/x", "src/y.ts") == ("repo", "src/x")
    assert sorted(load_tsconfig(repo).files) == ["a.json", "b.json", "tsconfig.json"]


def test_references_are_followed_four_levels_down_and_no_further(make_repo):
    chain = {f"r{n}.json": {"files": [], "references": [{"path": f"./r{n + 1}.json"}]} for n in range(1, 6)}
    chain["tsconfig.json"] = {"files": [], "references": [{"path": "./r1.json"}]}
    # r1 .. r4 are the four levels below the root; r5 is the fifth. Each leaf directory holds the project that covers it.
    chain["r4.json"] = {"files": [], "references": [{"path": "./r5.json"}], "compilerOptions": {"paths": {"four": ["./four/i.ts"]}}, "include": ["four"]}
    chain["r5.json"] = {"files": [], "references": [{"path": "./r6.json"}], "compilerOptions": {"paths": {"five": ["./five/i.ts"]}}, "include": ["five"]}
    modules, _ = modules_of(make_repo, {**chain, "four/i.ts": "", "four/u.ts": "", "five/i.ts": "", "five/u.ts": ""})
    assert modules.resolve("four", "four/u.ts") == ("repo", "four/i")
    assert modules.resolve("five", "five/u.ts")[0] != "repo"


def test_references_are_not_inherited_through_extends(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"extends": "./base.json", "files": []},
            "base.json": {"references": [{"path": "./tsconfig.app.json"}]},
            "tsconfig.app.json": APP,
            "src/App.tsx": "",
            "src/x.ts": "",
        },
    )
    assert modules.resolve("@/x", "src/App.tsx")[0] != "repo"


def test_a_config_that_covers_the_file_itself_governs_it_whatever_it_references(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"include": ["src"], "references": [{"path": "./tsconfig.app.json"}], "compilerOptions": {"paths": {"@/*": ["./root/*"]}}},
            "tsconfig.app.json": APP,
            "src/App.ts": "",
            "root/x.ts": "",
            "src/x.ts": "",
        },
    )
    assert modules.resolve("@/x", "src/App.ts") == ("repo", "root/x")


def test_an_alias_import_in_a_file_nothing_covers_is_unresolved_only_where_a_config_lists_references(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            **VITE,
            "scripts/build.ts": "",
            "other/tsconfig.json": {"include": ["x"], "compilerOptions": {"paths": {"@o/*": ["./x/*"]}}},
            "other/y.ts": "",
        },
    )
    kind, why = modules.resolve("@/lib/utils", "scripts/build.ts")
    assert kind == "unresolved"
    assert "@/lib/utils" in why and "tsconfig.app.json" in why
    # a name no config maps is a package, and the file is only as ungoverned as before
    assert modules.resolve("react", "scripts/build.ts") == ("package", "react")
    assert modules.resolve("@scope/pkg", "scripts/build.ts") == ("package", "@scope/pkg")
    kind, why = modules.resolve("@o/z", "other/y.ts")  # nothing covers it, and a config above it lists references
    assert kind == "unresolved" and "other/tsconfig.json" in why


def test_a_bare_star_path_does_not_make_every_package_import_in_an_uncovered_file_unresolved(make_repo):
    modules, _ = modules_of(
        make_repo,
        {
            "tsconfig.json": {"files": [], "references": [{"path": "./tsconfig.app.json"}]},
            "tsconfig.app.json": {"compilerOptions": {"paths": {"*": ["./types/*"]}}, "include": ["src"]},
            "types/known.ts": "",
            "src/a.ts": "",
            "scripts/b.ts": "",
        },
    )
    assert modules.resolve("react", "scripts/b.ts") == ("package", "react")
    assert modules.resolve("known", "scripts/b.ts")[0] == "unresolved"  # a repo file the `*` would reach


def test_an_alias_import_in_a_file_nothing_covers_stays_a_package_where_no_config_lists_references(make_repo):
    modules, _ = modules_of(
        make_repo,
        {"tsconfig.json": {"include": ["x"], "compilerOptions": {"paths": {"@o/*": ["./x/*"]}}}, "x/z.ts": "", "other/y.ts": ""},
    )
    assert modules.resolve("@o/z", "other/y.ts") == ("package", "@o/z")


def test_every_referenced_config_is_listed_so_an_edit_to_one_changes_the_fingerprint(make_repo):
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"files": [], "references": [{"path": "./tsconfig.app.json"}, {"path": "./pkg"}]}),
            "tsconfig.app.json": json.dumps({"extends": "./tsconfig.base.json", "include": ["src"]}),
            "tsconfig.base.json": "{}",
            "pkg/tsconfig.json": json.dumps({"references": [{"path": "../deep.json"}]}),
            "deep.json": "{}",
            "src/a.ts": "",
        }
    )
    assert set(fingerprint(repo, TYPESCRIPT)) == {
        "tsconfig.json", "tsconfig.app.json", "tsconfig.base.json", "pkg/tsconfig.json", "deep.json", "src/a.ts",
    }
    before = fingerprint(repo, TYPESCRIPT)
    (repo / "tsconfig.app.json").write_text(json.dumps({"include": ["src", "more"]}))
    assert fingerprint(repo, TYPESCRIPT) != before


def test_a_cache_written_before_references_were_followed_is_rebuilt(make_repo, tmp_path_factory):
    """Format 12: a file only a referenced project covers now has that project's aliases."""
    _edge_tests()
    from ripple import __version__
    from ripple.cache import cache_file, load_graph

    repo = make_repo({"m.ts": "export function f() {}\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir, profile=TYPESCRIPT)
    path = cache_file(repo.resolve(), cache_dir, TYPESCRIPT)
    saved = json.loads(path.read_text())
    assert saved["format"] == CACHE_FORMAT == f"{__version__}-19"
    saved["format"] = f"{__version__}-11"
    saved["graph"]["symbols"] = []
    path.write_text(json.dumps(saved))
    graph, cached = load_graph(repo, cache_dir, profile=TYPESCRIPT)
    assert cached is False and "m:f" in graph.symbols


def test_the_vite_app_gets_its_call_edge_through_the_referenced_project(make_repo):
    build_graph = _edge_tests()
    graph = build_graph(make_repo({k: v if isinstance(v, str) else json.dumps(v) for k, v in VITE.items()}), TYPESCRIPT)
    assert pairs(graph, "call") == {("src/App:App", "src/lib/utils:cn", "call")}


def test_a_call_through_an_import_no_config_covers_is_unresolved_with_the_reason_never_a_package_call(make_repo):
    build_graph = _edge_tests()
    files = {k: v if isinstance(v, str) else json.dumps(v) for k, v in VITE.items()}
    files["scripts/run.ts"] = "import { cn } from '@/lib/utils';\nexport function run() { return cn('a'); }\n"
    graph = build_graph(make_repo(files), TYPESCRIPT)
    assert pairs(graph, "call") == {("src/App:App", "src/lib/utils:cn", "call")}
    assert graph.external_calls == 0  # it is not code outside the repo: ripple does not know that
    reasons = [u.reason for u in graph.unresolved if u.caller == "scripts/run:run"]
    assert len(reasons) == 1 and "@/lib/utils" in reasons[0] and "tsconfig.app.json" in reasons[0]
