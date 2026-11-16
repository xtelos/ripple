"""Which module governs an import (ripple/go_modules.py): the main module of the build decides, never the module the importing file
is in; a replace with a version applies only where that version is the selected one; a vendor directory and a go.work change what
a path names. These read go.mod, go.work and vendor/modules.txt as text, so they need no parser and no Go toolchain; the probes
(bench/fixtures/go/probes/impl_chain_*, impl_replace_version*, impl_vendored, impl_workspace_*) hold the same rules to the compiler.
"""

from pathlib import Path

import pytest

from ripple.go_modules import MISSING, OUTSIDE, OWN, PUBLISHED, REPLACE, WORK, Modules, read_gomod


def modules_of(root: Path, files: dict[str, str]) -> Modules:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    return Modules(root, {"" if Path(rel).parent.as_posix() == "." else Path(rel).parent.as_posix() for rel in files})


def test_a_go_mod_is_read_by_its_grammar_blocks_quotes_comments_and_all():
    text = """
        module "example.com/m" // the module
        go 1.22.1
        toolchain go1.22.3
        require (
            example.com/a v1.0.0 // indirect
            "example.com/q" v1.1.0
        )
        require example.com/z v2.0.0+incompatible
        exclude example.com/a v0.9.0
        exclude (
            example.com/q v1.0.0
        )
        retract (
            v1.0.0 // published by mistake
            [v1.1.0, v1.2.0]
        )
        replace (
            example.com/a v1.0.0 => ./a // the checkout
            "example.com/b" => "./b"
            example.com/c => example.com/fork v1.0.0
        )
        replace example.com/d v0.1.0 => ../d
    """
    mod = read_gomod("sub", text)
    assert mod.path == "example.com/m" and mod.go == (1, 22)
    assert mod.requires == {"example.com/a": {"v1.0.0"}, "example.com/q": {"v1.1.0"}, "example.com/z": {"v2.0.0+incompatible"}}
    assert mod.excludes == {("example.com/a", "v0.9.0"), ("example.com/q", "v1.0.0")}
    assert mod.replaces == {
        "example.com/a": {"v1.0.0": "sub/a"},
        "example.com/b": {"": "sub/b"},
        "example.com/c": {"": None},
        "example.com/d": {"v0.1.0": "d"},
    }


def test_the_replace_of_one_version_does_not_hide_the_replace_of_another_and_a_comment_is_no_directive():
    mod = read_gomod("", "module m\nreplace m2 v1 => ./one\nreplace m2 => ./all // replace m2 v2 => ./two\n")
    assert mod.replaces == {"m2": {"v1": "one", "": "all"}}


def build(tmp_path: Path, main_go: str, replace: str, *, extra=None) -> Modules:
    files = {
        "go.mod": f"module example.com/main\n\ngo {main_go}\n\nrequire example.com/other v1.3.0\n\n{replace}\n",
        "m.go": "package main\n",
        "b/go.mod": "module example.com/other\n",
        "b/b.go": "package b\n",
        "c/go.mod": "module example.com/other\n",
        "c/c.go": "package c\n",
    }
    return modules_of(tmp_path, {**files, **(extra or {})})


def named(modules: Modules, path="example.com/other") -> tuple[str, str]:
    return modules.resolve_in(modules.mods[""], path, {"", "b", "c"})


@pytest.mark.parametrize(
    ("main_go", "replace", "want"),
    [
        ("1.22", "replace example.com/other => ./b", (REPLACE, "b")),
        ("1.22", "replace example.com/other v1.3.0 => ./b", (REPLACE, "b")),  # the selected version, listed in go.mod (go 1.17+)
        ("1.22", "replace example.com/other v1.2.0 => ./b", (PUBLISHED, "")),  # another version: the build takes the published one
        ("1.22", "replace (\n example.com/other v1.2.0 => ./c\n example.com/other => ./b\n)", (REPLACE, "b")),
        ("1.22", "replace (\n example.com/other v1.3.0 => ./c\n example.com/other => ./b\n)", (REPLACE, "c")),  # the exact version wins
        ("1.16", "replace example.com/other v1.3.0 => ./b", (MISSING, "")),  # the selected version is not read before go 1.17
        ("1.16", "replace example.com/other => ./b", (REPLACE, "b")),
        ("1.22", "replace example.com/other v1.3.0 => example.com/fork v1.0.0", (PUBLISHED, "")),  # a replacement from the network
        ("1.22", "exclude example.com/other v1.3.0\nreplace example.com/other v1.3.0 => ./b", (MISSING, "")),  # an excluded version moves the selection
        ("1.22", "replace example.com/other => ../outside", (PUBLISHED, "")),
    ],
)
def test_a_replace_with_a_version_applies_only_where_the_selected_version_is_known_to_match(tmp_path, main_go, replace, want):
    assert named(build(tmp_path, main_go, replace)) == want


def test_a_module_that_requires_a_version_twice_has_no_known_selection(tmp_path):
    modules = build(tmp_path, "1.22", "require example.com/other v1.4.0\nreplace example.com/other v1.3.0 => ./b")
    assert named(modules) == (MISSING, "")


def test_a_committed_vendor_directory_means_the_checkout_is_not_what_the_build_takes(tmp_path):
    vendored = build(tmp_path, "1.22", "replace example.com/other => ./b", extra={"vendor/modules.txt": "# example.com/other v1.3.0 => ./b\n"})
    assert named(vendored) == (MISSING, "")
    assert vendored.resolve_in(vendored.mods[""], "example.com/main", {""}) == (OWN, "")  # the main module's own package is no vendored one
    assert "vendor/modules.txt" in vendored.files, "the cache fingerprint must see the file that changes the answer"


def test_the_replaces_of_a_module_that_is_not_the_main_module_do_not_apply(tmp_path):
    modules = modules_of(
        tmp_path,
        {
            "go.mod": "module example.com/main\n\nrequire example.com/a v0.0.0\nrequire example.com/other v1.3.0\n\nreplace example.com/a => ./a\n",
            "a/go.mod": "module example.com/a\n\nrequire example.com/other v1.3.0\n\nreplace example.com/other => ../b\n",
            "b/go.mod": "module example.com/other\n",
        },
    )
    root, a = modules.mods[""], modules.mods["a"]
    dirs = {"", "a", "b"}
    assert modules.resolve_in(root, "example.com/a", dirs) == (REPLACE, "a")
    assert modules.resolve_in(root, "example.com/other", dirs) == (PUBLISHED, ""), "a's replace is ignored in a build whose main module is the root"
    assert modules.resolve_in(a, "example.com/other", dirs) == (REPLACE, "b")
    # a file of module a is in both builds, and they disagree: a call edge from it has no one callee
    assert modules.governors(a) == [root, a] or set(modules.governors(a)) == {root, a}
    assert modules.resolve_agreed("a", "example.com/other", dirs) == (MISSING, "")
    assert modules.resolve_agreed("a", "example.com/a", dirs)[0] in (REPLACE, OWN)
    assert modules.resolve_agreed("", "example.com/other", dirs) == (PUBLISHED, "")  # nothing replaces the root: one build, one answer


def test_the_main_module_replaces_a_dependency_of_a_dependency(tmp_path):
    modules = modules_of(
        tmp_path,
        {
            "go.mod": "module example.com/main\n\nreplace example.com/a => ./a\nreplace example.com/other => ./b\n",
            "a/go.mod": "module example.com/a\n\nrequire example.com/other v1.3.0\n",
            "b/go.mod": "module example.com/other\n",
        },
    )
    assert modules.resolve_in(modules.mods[""], "example.com/other", {"", "a", "b"}) == (REPLACE, "b")
    assert modules.resolve_in(modules.mods["a"], "example.com/other", {"", "a", "b"}) == (PUBLISHED, "")


WORKSPACE = {
    "ws/go.work": "go 1.22\n\nuse (\n\t./a\n\t./b\n)\n",
    "ws/a/go.mod": "module example.com/a\n\nreplace example.com/c => ../../c\n",
    "ws/b/go.mod": "module example.com/b\n",
    "c/go.mod": "module example.com/c\n",
    "d/go.mod": "module example.com/d\n",
}


def test_a_workspace_applies_the_replaces_of_every_member_and_its_members_build_against_each_other(tmp_path):
    modules = modules_of(tmp_path, WORKSPACE)
    a, b = modules.mods["ws/a"], modules.mods["ws/b"]
    dirs = {"ws/a", "ws/b", "c", "d"}
    assert modules.resolve_in(b, "example.com/c", dirs) == (REPLACE, "c"), "a's replace binds the whole workspace"
    assert modules.resolve_in(b, "example.com/a", dirs) == (WORK, "ws/a")
    assert modules.resolve_in(b, "example.com/d", dirs) == (PUBLISHED, ""), "d is neither a member nor replaced"
    assert modules.run_key(a) == modules.run_key(b) == "work:ws"


def test_the_go_work_replace_wins_over_a_member_and_two_members_that_differ_are_a_conflict(tmp_path):
    work = dict(WORKSPACE, **{"ws/go.work": "go 1.22\n\nuse (\n\t./a\n\t./b\n)\n\nreplace example.com/c => ../c2\n", "c2/go.mod": "module example.com/c\n"})
    modules = modules_of(tmp_path / "one", work)
    assert modules.resolve_in(modules.mods["ws/b"], "example.com/c", {"ws/a", "ws/b", "c", "c2"}) == (REPLACE, "c2")
    clash = dict(WORKSPACE, **{"ws/b/go.mod": "module example.com/b\n\nreplace example.com/c => ../../c2\n", "c2/go.mod": "module example.com/c\n"})
    modules = modules_of(tmp_path / "two", clash)
    assert modules.resolve_in(modules.mods["ws/b"], "example.com/c", {"ws/a", "ws/b", "c", "c2"}) == (MISSING, "")


def test_a_versioned_replace_is_never_known_to_apply_in_a_workspace(tmp_path):
    files = dict(WORKSPACE, **{"ws/a/go.mod": "module example.com/a\n\ngo 1.22\n\nrequire example.com/c v1.0.0\n\nreplace example.com/c v1.0.0 => ../../c\n"})
    modules = modules_of(tmp_path, files)
    assert modules.resolve_in(modules.mods["ws/b"], "example.com/c", {"ws/a", "ws/b", "c"}) == (MISSING, "")


def test_a_path_no_module_of_the_repo_names_is_outside_and_a_file_in_no_module_is_not_provable(tmp_path):
    modules = modules_of(tmp_path, {"go.mod": "module example.com/main\n"})
    assert modules.resolve_in(modules.mods[""], "github.com/other/lib", {""}) == (OUTSIDE, "")
    assert modules.resolve_in(None, "example.com/main", {""}) == (MISSING, "")
