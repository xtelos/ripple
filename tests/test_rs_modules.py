"""rs_modules: module ids, the crate graph read from Cargo.toml, and the mod tree's honest gaps.

None of the manifest tests needs a parser; the tree tests parse with tree-sitter-rust and skip without it.
"""

import pytest

from ripple import rs_modules as rm

CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("src/lib.rs", "src/lib"),
        ("src/main.rs", "src/main"),
        ("src/x/mod.rs", "src/x"),
        ("src/x.rs", "src/x"),
        ("mod.rs", "mod"),  # a root mod.rs has no directory to stand for
        ("src/a/b/mod.rs", "src/a/b"),
    ],
)
def test_module_id_folds_mod_rs_into_its_directory_and_nothing_else(rel, expected):
    assert rm.module_id(rel) == expected


def sources(repo, *rels):
    return [repo / rel for rel in rels]


def test_the_crates_of_a_package_are_found_as_cargo_finds_them(make_repo):
    repo = make_repo(
        {
            "Cargo.toml": CARGO + '[[bin]]\nname = "tool-x"\npath = "tools/x.rs"\n[[example]]\nname = "demo"\npath = "demo/main.rs"\n',
            "src/lib.rs": "",
            "src/main.rs": "",
            "src/bin/a.rs": "",
            "src/bin/b/main.rs": "",
            "tests/t1.rs": "",
            "tests/t2/main.rs": "",
            "tools/x.rs": "",
            "demo/main.rs": "",
            "benches/bench1.rs": "",
            "build.rs": "",
        }
    )
    workspace = rm.load_workspace(repo, sources(repo, "src/lib.rs"))
    got = sorted((c.kind, c.name, c.root) for c in workspace.crates)
    assert got == sorted(
        [
            ("lib", "app", "src/lib.rs"), ("bin", "app", "src/main.rs"), ("bin", "a", "src/bin/a.rs"), ("bin", "b", "src/bin/b/main.rs"),
            ("bin", "tool_x", "tools/x.rs"), ("test", "t1", "tests/t1.rs"), ("test", "t2", "tests/t2/main.rs"),
            ("example", "demo", "demo/main.rs"), ("bench", "bench1", "benches/bench1.rs"), ("build", "build_script_build", "build.rs"),
        ]
    )  # fmt: skip


def test_autodiscovery_can_be_turned_off(make_repo):
    repo = make_repo({"Cargo.toml": CARGO.replace("[package]", "[package]\nautobins = false\nautotests = false"), "src/main.rs": "", "tests/t.rs": ""})
    assert rm.load_workspace(repo, sources(repo, "src/main.rs")).crates == []


def test_dependencies_by_the_name_they_are_written_as(make_repo):
    repo = make_repo(
        {
            "Cargo.toml": '[workspace]\nmembers = ["app", "my-lib", "other"]\n[workspace.dependencies]\nshared = { path = "other" }\n'
            '[patch.crates-io]\nregistry-dep = { path = "other" }\n',
            "my-lib/Cargo.toml": '[package]\nname = "my-lib"\nversion = "0.1.0"\n',
            "my-lib/src/lib.rs": "",
            "other/Cargo.toml": '[package]\nname = "other"\nversion = "0.1.0"\n',
            "other/src/lib.rs": "",
            "app/Cargo.toml": CARGO + '[dependencies]\nmy-lib = { path = "../my-lib" }\nrenamed = { package = "my-lib", path = "../my-lib" }\n'
            'shared.workspace = true\nregistry-dep = "1"\nserde = "1"\n[dev-dependencies]\nserde = "1"\n',
            "app/src/lib.rs": "",
        }
    )
    deps = rm.load_workspace(repo, sources(repo, "app/src/lib.rs", "my-lib/src/lib.rs", "other/src/lib.rs")).packages["app"].deps
    assert deps == {"my_lib": "my-lib", "renamed": "my-lib", "shared": "other", "registry_dep": "other", "serde": None}


def test_a_name_two_sections_give_different_meanings_is_ambiguous(make_repo):
    repo = make_repo(
        {
            "Cargo.toml": '[workspace]\nmembers = ["app", "dep"]\n',
            "dep/Cargo.toml": '[package]\nname = "dep"\nversion = "0.1.0"\n',
            "dep/src/lib.rs": "",
            "app/Cargo.toml": CARGO + '[dependencies]\ndep = "1"\n[dev-dependencies]\ndep = { path = "../dep" }\n',
            "app/src/lib.rs": "",
        }
    )
    assert rm.load_workspace(repo, sources(repo, "app/src/lib.rs", "dep/src/lib.rs")).packages["app"].deps == {"dep": rm.AMBIGUOUS}


def test_a_path_that_climbs_to_the_repo_root_names_the_root_package(make_repo):
    repo = make_repo(
        {
            "Cargo.toml": CARGO,
            "src/lib.rs": "",
            "bench/Cargo.toml": '[package]\nname = "bench"\nversion = "0.1.0"\n[dependencies]\napp = { path = "../" }\n',
            "bench/src/lib.rs": "",
        }
    )
    assert rm.load_workspace(repo, sources(repo, "bench/src/lib.rs", "src/lib.rs")).packages["bench"].deps == {"app": ""}


def test_the_edition_comes_from_the_package_or_its_workspace_and_defaults_to_2015(make_repo):
    repo = make_repo(
        {
            "Cargo.toml": '[workspace]\nmembers = ["a", "b", "c"]\n[workspace.package]\nedition = "2021"\n',
            "a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\nedition.workspace = true\n',
            "b/Cargo.toml": '[package]\nname = "b"\nversion = "0.1.0"\nedition = "2018"\n',
            "c/Cargo.toml": '[package]\nname = "c"\nversion = "0.1.0"\n',
            "a/src/lib.rs": "", "b/src/lib.rs": "", "c/src/lib.rs": "",
        }
    )  # fmt: skip
    packages = rm.load_workspace(repo, sources(repo, "a/src/lib.rs", "b/src/lib.rs", "c/src/lib.rs")).packages
    assert {name: p.edition for name, p in packages.items()} == {"a": "2021", "b": "2018", "c": "2015"}


def test_a_manifest_above_no_source_is_not_a_config_the_index_depends_on(make_repo):
    repo = make_repo({"Cargo.toml": CARGO, "src/lib.rs": "", "docs/Cargo.toml": CARGO, "docs/readme.md": "x"})
    assert rm.config_files(repo, sources(repo, "src/lib.rs")) == ["Cargo.toml"]


# ---- the mod tree (needs the parser)


def tree_of(repo, root, walked):
    tsr = pytest.importorskip("tree_sitter_rust")
    import tree_sitter as ts

    parser = ts.Parser(ts.Language(tsr.language()))
    cache = {}

    def parse(rel):
        if rel not in cache:
            src = (repo / rel).read_bytes()
            cache[rel] = (src, parser.parse(src).root_node)
        return cache[rel]

    crate = rm.Crate("app", "lib", root, None)
    return rm.build_tree(repo, crate, set(walked), parse)


def test_a_file_a_cfg_attr_path_names_and_a_declaration_with_no_file_are_problems_not_silence(make_repo):
    repo = make_repo(
        {
            "src/lib.rs": '#[cfg_attr(unix, path = "u.rs")]\nmod platform;\nmod gone;\nmod fine;\n',
            "src/fine.rs": "",
        }
    )
    tree = tree_of(repo, "src/lib.rs", ["src/lib.rs", "src/fine.rs"])
    assert sorted(m.file for m in tree.modules) == ["src/fine.rs", "src/lib.rs"]
    assert len(tree.problems) == 2
    assert any("`mod platform;` names no file" in p and "cfg_attr path" in p for p in tree.problems)
    assert any("`mod gone;` names no file" in p for p in tree.problems)


def test_a_file_the_walk_did_not_list_is_a_problem_not_read(make_repo):
    repo = make_repo({"src/lib.rs": "mod hidden;\n", "src/hidden.rs": "pub fn f() {}\n"})
    tree = tree_of(repo, "src/lib.rs", ["src/lib.rs"])
    assert [m.file for m in tree.modules] == ["src/lib.rs"]
    assert tree.problems == ["src/hidden.rs (declared by `mod hidden;` in src/lib.rs, but outside the files ripple walks)"]


def test_a_non_mod_rs_file_owns_the_directory_named_for_it_and_a_path_file_owns_its_own(make_repo):
    repo = make_repo(
        {
            "src/lib.rs": 'mod a;\n#[path = "odd/p.rs"]\nmod p;\n',
            "src/a.rs": "mod b;\n",
            "src/a/b.rs": "",
            "src/odd/p.rs": "mod q;\n",
            "src/odd/q.rs": "",
        }
    )
    walked = ["src/lib.rs", "src/a.rs", "src/a/b.rs", "src/odd/p.rs", "src/odd/q.rs"]
    tree = tree_of(repo, "src/lib.rs", walked)
    assert sorted(m.file for m in tree.modules) == sorted(walked) and tree.problems == []
