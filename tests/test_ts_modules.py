"""Which file a TypeScript import names, and what a file's module id is. No parser needed."""

import json

import pytest

from ripple.indexer import find_source_files
from ripple.profile import TYPESCRIPT
from ripple.ts_modules import ModuleMap, load_tsconfig, module_id, strip_jsonc


@pytest.mark.parametrize(
    ("path", "module"),
    [
        ("src/util/math.ts", "src/util/math"),
        ("src/util/index.ts", "src/util"),  # an index file is its directory
        ("src/ui/Page.tsx", "src/ui/Page"),
        ("index.ts", "index"),  # at the root there is no directory to be
        ("tests/inventory.test.ts", "tests/inventory.test"),  # the dot is part of the name
        ("lib/a.mts", "lib/a"),
        ("lib/b.cts", "lib/b"),
        ("src/index.d.ts", "src/index.d"),  # never indexed (the walk leaves .d.ts out), but not mistaken for index
        ("src/indexes.ts", "src/indexes"),
    ],
)
def test_a_module_is_its_path_without_the_extension_and_an_index_is_its_directory(path, module):
    assert module_id(path) == module


def test_strip_jsonc_removes_comments_and_trailing_commas_and_nothing_inside_strings():
    text = """﻿{
      // a line comment
      "compilerOptions": { /* block
         comment */ "baseUrl": ".", "url": "http://example.com/a//b", "q": "a \\" // not a comment",
        "paths": { "@x/*": ["src/*",], },
      },
      "include": ["src",],
    }"""
    assert json.loads(strip_jsonc(text)) == {
        "compilerOptions": {
            "baseUrl": ".",
            "url": "http://example.com/a//b",
            "q": 'a " // not a comment',
            "paths": {"@x/*": ["src/*"]},
        },
        "include": ["src"],
    }
    assert json.loads(strip_jsonc('{"a": [1, 2,\n]}')) == {"a": [1, 2]}


def mapper(make_repo, files, tsconfig=None):
    sources = {path: "" for path in files}
    if tsconfig is not None:
        sources["tsconfig.json"] = tsconfig if isinstance(tsconfig, str) else json.dumps(tsconfig)
    repo = make_repo(sources)
    return ModuleMap(repo, find_source_files(repo, TYPESCRIPT)), repo


def test_relative_imports_with_and_without_an_extension_and_to_a_directory(make_repo):
    modules, _ = mapper(
        make_repo,
        [
            "src/a.ts",
            "src/b.tsx",
            "src/util/index.ts",
            "src/util/math.ts",
            "src/m.mts",
            "src/c.cts",
            "src/deep/er/x.ts",
            "src/foo.test.ts",
            "index.ts",
        ],
    )
    here = "src/a.ts"
    assert modules.resolve("./b", here) == ("repo", "src/b")  # no extension: .ts, then .tsx
    assert modules.resolve("./util", here) == ("repo", "src/util")  # a directory is its index
    assert modules.resolve("./util/", here) == ("repo", "src/util")
    assert modules.resolve("./util/index", here) == ("repo", "src/util")
    assert modules.resolve("./util/math.ts", here) == ("repo", "src/util/math")  # allowImportingTsExtensions
    assert modules.resolve("./util/math.js", here) == ("repo", "src/util/math")  # the extension it will have built
    assert modules.resolve("./b.jsx", here) == ("repo", "src/b")
    assert modules.resolve("./m.mjs", here) == ("repo", "src/m")
    assert modules.resolve("./c.cjs", here) == ("repo", "src/c")
    assert modules.resolve("./foo.test", here) == ("repo", "src/foo.test")  # a dotted name is not an extension
    assert modules.resolve("../index", "src/deep/er/x.ts") == ("missing", "../index")  # that is src/deep/index
    assert modules.resolve("../../../index", "src/deep/er/x.ts") == ("repo", "index")  # the root's index.ts
    assert modules.resolve(".", "src/util/math.ts") == ("repo", "src/util")  # the directory this file is in


def test_a_relative_import_that_names_no_source_file_is_not_a_guess(make_repo):
    modules, _ = mapper(make_repo, ["src/a.ts", "src/data.json", "src/legacy.js", "src/styles.css"])
    here = "src/a.ts"
    assert modules.resolve("./nothing", here) == ("missing", "./nothing")
    # a file that exists but is not source (json, css) is outside what ripple reads
    assert modules.resolve("./data.json", here) == ("package", "./data.json")
    assert modules.resolve("./legacy.js", here)[0] == "unresolved"  # a JavaScript file: the other language's, not a package
    assert modules.resolve("./styles.css", here) == ("package", "./styles.css")
    assert modules.resolve("../../outside", here) == ("package", "../../outside")  # above the repo root


def test_bare_specifiers_are_packages_unless_an_alias_names_a_repo_file(make_repo):
    modules, _ = mapper(make_repo, ["src/a.ts", "src/policy.ts"])
    assert modules.resolve("react", "src/a.ts") == ("package", "react")
    assert modules.resolve("node:fs", "src/a.ts") == ("package", "node:fs")
    assert modules.resolve("@wh/policy", "src/a.ts") == ("package", "@wh/policy")  # no tsconfig, no alias
    assert modules.resolve("/abs/path", "src/a.ts") == ("package", "/abs/path")


def test_tsconfig_paths_resolve_wildcards_exact_names_and_prefer_the_longest_pattern(make_repo):
    modules, _ = mapper(
        make_repo,
        ["src/a.ts", "src/policy.ts", "src/ui/Button.tsx", "src/ui/index.ts", "lib/special/x.ts", "lib/y.ts", "shared/s.ts"],
        {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {
                    "@wh/*": ["src/*"],
                    "@wh/ui": ["src/ui"],  # exact, and the more specific
                    "@lib/*": ["nowhere/*", "lib/*"],  # tried in order: the first that exists wins
                    "@special/*": ["lib/special/*"],
                    "@all/*": ["lib/*"],
                    "@all/special/*": ["lib/special/*"],
                    "shared": ["shared/s.ts"],
                },
            }
        },
    )
    here = "src/a.ts"
    assert modules.resolve("@wh/policy", here) == ("repo", "src/policy")
    assert modules.resolve("@wh/ui/Button", here) == ("repo", "src/ui/Button")
    assert modules.resolve("@wh/ui", here) == ("repo", "src/ui")
    assert modules.resolve("@lib/y", here) == ("repo", "lib/y")
    assert modules.resolve("@special/x", here) == ("repo", "lib/special/x")
    assert modules.resolve("@all/special/x", here) == ("repo", "lib/special/x")  # not lib/special/x via @all/*
    assert modules.resolve("@all/y", here) == ("repo", "lib/y")
    assert modules.resolve("shared", here) == ("repo", "shared/s")
    assert modules.resolve("@wh/missing", here) == ("package", "@wh/missing")  # no file: not a repo module


def test_baseurl_resolves_bare_specifiers_and_paths_without_it_are_relative_to_the_config(make_repo):
    modules, _ = mapper(
        make_repo,
        ["src/a.ts", "src/models/item.ts", "src/lib/z.ts"],
        '{ "compilerOptions": { "baseUrl": "src", // comment\n "paths": { "@z": ["lib/z"], }, }, }',
    )
    assert modules.resolve("models/item", "src/a.ts") == ("repo", "src/models/item")  # from baseUrl src
    assert modules.resolve("@z", "src/a.ts") == ("repo", "src/lib/z")  # targets are relative to baseUrl
    assert modules.resolve("react", "src/a.ts") == ("package", "react")

    modules, _ = mapper(make_repo, ["app/a.ts", "app/b.ts"], {"compilerOptions": {"paths": {"~/*": ["./app/*"]}}})
    assert modules.resolve("~/b", "app/a.ts") == ("repo", "app/b")  # no baseUrl: relative to tsconfig.json


def test_an_extends_by_relative_path_is_followed_and_one_by_package_name_is_not(make_repo):
    repo = make_repo(
        {
            "config/base.json": json.dumps({"compilerOptions": {"baseUrl": "..", "paths": {"@a/*": ["src/*"]}}}),
            "src/a.ts": "",
            "src/b.ts": "",
            "tsconfig.json": json.dumps({"extends": "./config/base", "compilerOptions": {"strict": True}}),
        }
    )
    modules = ModuleMap(repo, find_source_files(repo, TYPESCRIPT))
    assert modules.resolve("@a/b", "src/a.ts") == ("repo", "src/b")  # baseUrl ".." in config/ is the repo root

    repo2 = make_repo({"src/a.ts": "", "tsconfig.json": json.dumps({"extends": "@tsconfig/node18/tsconfig.json"})})
    assert load_tsconfig(repo2).paths == {}


def test_a_tsconfig_that_cannot_be_read_is_an_empty_one_with_the_reason(make_repo, tmp_path_factory):
    repo = make_repo({"src/a.ts": "", "tsconfig.json": "{ this is not json"})
    config = load_tsconfig(repo)
    assert config.paths == {} and config.base_url is None
    assert "tsconfig.json could not be read" in config.problem
    assert load_tsconfig(tmp_path_factory.mktemp("no-config")).problem == ""  # no config is not a problem


# ---- two files with one module id, a baseUrl outside the repo, a hostile chain of configs


def test_two_files_that_fold_to_one_module_id_each_get_an_id_and_the_one_an_import_reaches_keeps_it(make_repo):
    repo = make_repo({name: "" for name in ("a.ts", "a/index.ts", "b.tsx", "b.ts", "m.mts", "m.cts")})
    modules = ModuleMap(repo, find_source_files(repo, TYPESCRIPT))
    assert modules.modules == {
        "a.ts": "a",  # the compiler tries the file before the directory
        "a/index.ts": "a/index",
        "b.ts": "b",  # .ts before .tsx
        "b.tsx": "b.tsx",  # `b` is taken, so the whole path
        "m.mts": "m",
        "m.cts": "m.cts",
    }
    assert sorted(modules.shadowed) == ["a/index.ts", "b.tsx", "m.cts"]
    assert modules.resolve("./a", "main.ts") == ("repo", "a")
    assert modules.resolve("./a/index", "main.ts") == ("repo", "a/index")  # the directory's own file, by its own path


def test_a_clash_that_leaves_no_clean_id_still_gives_every_file_its_own(make_repo):
    repo = make_repo({name: "" for name in ("a.ts", "a.tsx", "a/index.ts", "a/index.tsx", "a.tsx.ts")})
    modules = ModuleMap(repo, find_source_files(repo, TYPESCRIPT))
    assert len(set(modules.modules.values())) == len(modules.modules) == 5
    assert modules.modules["a.ts"] == "a"


def test_a_base_url_outside_the_repo_takes_its_paths_with_it_and_names_nothing_in_the_repo(make_repo):
    repo = make_repo(
        {
            "src/util.ts": "",
            "main.ts": "",
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "../shared", "paths": {"@lib/*": ["src/*"]}}}),
        }
    )
    modules = ModuleMap(repo, find_source_files(repo, TYPESCRIPT))
    # the compiler resolves @lib/util to ../shared/src/util.ts: the repo's src/util.ts is not it
    assert modules.resolve("@lib/util", "main.ts") == ("package", "@lib/util")
    assert modules.resolve("src/util", "main.ts") == ("package", "src/util")


def test_a_config_that_extends_itself_or_names_one_config_many_times_is_read_once(make_repo):
    import time

    repo = make_repo({"tsconfig.json": json.dumps({"extends": ["./tsconfig.json"] * 4, "compilerOptions": {"baseUrl": "."}})})
    started = time.monotonic()
    config = load_tsconfig(repo)
    assert time.monotonic() - started < 1  # four names at eight levels read the file 65,536 times
    assert config.files == ["tsconfig.json"] and config.base_url == "." and config.problem == ""


def test_a_long_chain_of_configs_is_read_only_so_far(make_repo):
    files = {f"c{i}.json": json.dumps({"extends": f"./c{i + 1}"}) for i in range(40)}
    files["c40.json"] = json.dumps({"compilerOptions": {"baseUrl": "."}})
    files["tsconfig.json"] = json.dumps({"extends": "./c0"})
    config = load_tsconfig(make_repo(files))
    assert len(config.files) == 16 and config.files[0] == "tsconfig.json"


def test_a_config_that_is_a_symlink_out_of_the_repo_is_not_read(make_repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.json"
    outside.write_text(json.dumps({"compilerOptions": {"baseUrl": "x", "paths": {"@o/*": ["o/*"]}}}))
    repo = make_repo({"src/a.ts": ""})
    (repo / "tsconfig.json").symlink_to(outside)
    assert load_tsconfig(repo) == load_tsconfig(tmp_path_factory.mktemp("none"))


def test_an_extended_config_that_is_a_symlink_out_of_the_repo_is_not_read(make_repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.json"
    outside.write_text(json.dumps({"compilerOptions": {"baseUrl": "x", "paths": {"@o/*": ["o/*"]}}}))
    repo = make_repo({"src/b.ts": "", "tsconfig.json": json.dumps({"extends": "./link"})})
    (repo / "link.json").symlink_to(outside)
    config = load_tsconfig(repo)
    assert config.paths == {} and config.base_url is None and config.files == ["tsconfig.json"]


def test_every_config_that_was_read_is_listed_so_an_edit_to_one_changes_the_fingerprint(make_repo):
    from ripple.cache import fingerprint

    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"extends": ["./a", "./sub/b.json"]}),
            "a.json": json.dumps({"extends": "./sub/b"}),
            "sub/b.json": "{}",
            "src/x.ts": "",
            "other.json": "{}",
        }
    )
    assert load_tsconfig(repo).files == ["tsconfig.json", "a.json", "sub/b.json"]
    assert {"tsconfig.json", "a.json", "sub/b.json", "src/x.ts"} == set(fingerprint(repo, TYPESCRIPT))
