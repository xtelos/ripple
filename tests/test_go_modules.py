"""Which package a directory is and which directory an import path names (ripple/go_modules.py, ripple/go_indexer.py).

These read go.mod, go.work and file names as text, so they need no parser.
"""

from pathlib import Path

import pytest

from ripple.go_indexer import config_files, module_id, platform_file, reads_as_source
from ripple.go_modules import MISSING, OUTSIDE, OWN, PUBLISHED, REPLACE, WORK, Modules, read_gomod, read_gowork
from ripple.profile import GO


def test_a_go_mod_gives_its_module_path_and_only_its_local_replaces():
    text = """
        // a comment
        module "example.com/app"  // trailing

        go 1.22

        require example.com/lib v1.0.0

        replace example.com/lib v1.0.0 => ../lib
        replace (
            example.com/a => ./vendored/a
            example.com/b v1.2.3 => example.com/b-fork v1.2.4
            example.com/c => /abs/c
            example.com/out => ../../../outside
        )
    """
    mod = read_gomod("services/app", text)
    assert (mod.path, mod.dir) == ("example.com/app", "services/app")
    assert mod.replaces == {"example.com/lib": "services/lib", "example.com/a": "services/app/vendored/a"}  # the others are not repo directories


def test_a_go_mod_with_no_module_line_is_not_a_module():
    assert read_gomod("", "go 1.22\n") is None


def test_a_go_work_lists_its_used_directories_and_its_local_replaces():
    uses, replaces = read_gowork("w", "go 1.22\nuse ./a\nuse (\n\t./b\n\t../w2\n)\nreplace x.com/m => ./m\n")
    assert uses == {"w/a", "w/b", "w2"} and replaces == {"x.com/m": "w/m"}


def write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


DIRS = {"", "lib", "lib/util", "app", "tool", "wsapp", "nested", "nested/inner", "sub/pkg"}
LAYOUT = {
    "go.mod": "module ex.com/root\n",
    "sub/pkg/p.go": "package pkg\n",
    "lib/go.mod": "module ex.com/lib\n",
    "lib/util/u.go": "package util\n",
    "app/go.mod": "module ex.com/app\nrequire ex.com/lib v1.2.0\n",
    "tool/go.mod": "module ex.com/tool\nreplace ex.com/lib => ../lib\n",
    "wsapp/go.mod": "module ex.com/wsapp\n",
    "go.work": "use (\n\t./lib\n\t./wsapp\n)\n",
    "nested/go.mod": "module ex.com/root/nested\n",
    "nested/inner/i.go": "package inner\n",
}


@pytest.fixture
def modules(tmp_path):
    write(tmp_path, LAYOUT)
    return Modules(tmp_path, DIRS)


def test_a_directorys_package_is_its_modules_path_below_the_nearest_go_mod(modules):
    assert modules.import_path("") == "ex.com/root"
    assert modules.import_path("sub/pkg") == "ex.com/root/sub/pkg"
    assert modules.import_path("lib/util") == "ex.com/lib/util"
    assert modules.import_path("nested/inner") == "ex.com/root/nested/inner"  # the nested go.mod cuts the subtree out of root
    assert modules.import_path("nowhere") == "ex.com/root/nowhere"


def test_a_directory_with_no_go_mod_above_it_has_no_import_path(tmp_path):
    write(tmp_path, {"a/a.go": "package a\n"})
    assert Modules(tmp_path, {"a"}).import_path("a") is None


PACKAGES = {"", "lib/util", "sub/pkg", "nested/inner"}


@pytest.mark.parametrize(
    "importer, path, how, target",
    [
        ("", "ex.com/root/sub/pkg", OWN, "sub/pkg"),  # the importer's own module
        ("sub/pkg", "ex.com/root", OWN, ""),
        ("app", "ex.com/lib/util", PUBLISHED, ""),  # app requires lib v1.2.0 with no replace and is not in the workspace
        ("tool", "ex.com/lib/util", REPLACE, "lib/util"),  # replace ex.com/lib => ../lib
        ("wsapp", "ex.com/lib/util", WORK, "lib/util"),  # go.work lists lib and wsapp
        ("tool", "ex.com/lib/missing", MISSING, ""),  # inside a visible repo module, naming no package ripple read
        ("", "ex.com/root/nested/inner", PUBLISHED, ""),  # the nested module, which root neither replaces nor uses
        ("nested", "ex.com/root/sub/pkg", PUBLISHED, ""),  # root's package, which nested is not told about
        ("", "fmt", OUTSIDE, ""),
        ("", "github.com/spf13/cobra", OUTSIDE, ""),
    ],
)
def test_what_an_import_names(modules, importer, path, how, target):
    assert modules.resolve(importer, path, PACKAGES) == (how, target)


def test_a_replace_to_a_directory_whose_module_has_another_path_is_not_followed(tmp_path):
    write(tmp_path, {"a/go.mod": "module ex.com/a\nreplace ex.com/b => ../b\n", "b/go.mod": "module ex.com/other\n", "b/x/x.go": "package x\n"})
    modules = Modules(tmp_path, {"a", "b/x"})
    assert modules.resolve("a", "ex.com/b/x", {"b/x"}) == (OUTSIDE, "")  # no repo module is named ex.com/b


def test_the_longest_module_path_wins(tmp_path):
    write(tmp_path, {"go.mod": "module ex.com/m\n", "n/go.mod": "module ex.com/m/n\n", "n/p/p.go": "package p\n", "x.go": "package m\n"})
    modules = Modules(tmp_path, {"", "n/p"})
    # root's own import of a path inside the nested module is not root's package: nested is built from its published version
    assert modules.resolve("", "ex.com/m/n/p", {"n/p"}) == (PUBLISHED, "")


def test_a_vendor_directory_below_a_module_is_not_a_source_directory(tmp_path):
    write(tmp_path, {"go.mod": "module ex.com/m\n", "a.go": "package m\n", "vendor/d/d.go": "package d\n", "x/vendor/d/d.go": "package d\n"})
    ok = lambda rel: reads_as_source(tmp_path / rel, GO, tmp_path)  # noqa: E731
    assert ok("a.go") and not ok("vendor/d/d.go") and not ok("x/vendor/d/d.go")


def test_testdata_underscore_and_dot_names_are_not_read(tmp_path):
    write(tmp_path, {"go.mod": "module ex.com/m\n", "ok.go": "package m\n", "testdata/t.go": "package t\n", "_x/s.go": "package s\n", "_hidden.go": "package m\n"})
    ok = lambda rel: reads_as_source(tmp_path / rel, GO, tmp_path)  # noqa: E731
    assert ok("ok.go") and not ok("testdata/t.go") and not ok("_x/s.go") and not ok("_hidden.go")


def test_only_a_file_that_says_build_ignore_is_a_script(tmp_path):
    write(
        tmp_path,
        {
            "go.mod": "module ex.com/m\n",
            "gen.go": "//go:build ignore\n\npackage main\n",
            "tagged.go": "//go:build linux\n\npackage m\n",
            "both.go": "//go:build ignore || linux\n\npackage m\n",
            "text.go": "package m\n// go:build ignore is only a comment here\n",
        },
    )
    ok = lambda rel: reads_as_source(tmp_path / rel, GO, tmp_path)  # noqa: E731
    assert not ok("gen.go") and ok("tagged.go") and ok("both.go") and ok("text.go")


def test_the_cache_fingerprints_every_go_mod_and_go_work_above_a_source_directory(tmp_path):
    write(tmp_path, LAYOUT)
    sources = [tmp_path / "lib/util/u.go", tmp_path / "sub/pkg/p.go", tmp_path / "nested/inner/i.go"]
    assert config_files(tmp_path, sources) == ["go.mod", "go.work", "lib/go.mod", "nested/go.mod"]


def test_a_query_path_names_the_directory_part_of_a_file():
    assert module_id("doc/man_docs.go") == "doc" and module_id("x.go") == ""


def test_a_module_fixture_below_testdata_is_not_read_wherever_its_go_mod_is(tmp_path):
    write(
        tmp_path,
        {
            "go.mod": "module ex.com/m\n",
            "lib/lib.go": "package lib\n",
            "lib/testdata/fx/go.mod": "module ex.com/m\n",
            "lib/testdata/fx/b.go": "package lib\n",
            "tools/_gen/go.mod": "module ex.com/gen\n",
            "tools/_gen/g.go": "package gen\n",
        },
    )
    ok = lambda rel: reads_as_source(tmp_path / rel, GO, tmp_path)  # noqa: E731
    assert ok("lib/lib.go") and not ok("lib/testdata/fx/b.go") and not ok("tools/_gen/g.go")


def test_a_repo_below_a_directory_named_testdata_or_vendor_is_still_read(tmp_path):
    repo = tmp_path / "testdata" / "vendor" / "checkout"
    write(repo, {"go.mod": "module ex.com/m\n", "a.go": "package m\n"})
    assert reads_as_source(repo / "a.go", GO, repo)


def test_a_file_no_build_includes_is_not_read_but_one_some_build_includes_is(tmp_path):
    write(
        tmp_path,
        {
            "go.mod": "module ex.com/m\n",
            "never.go": "//go:build ignore && windows\n\npackage m\n",
            "legacy.go": "// +build ignore\n\npackage main\n",
            "not_ignored.go": "//go:build !ignore\n\npackage m\n",
            "either.go": "//go:build (ignore || linux) && !windows\n\npackage m\n",
            "options.go": "// +build linux ignore\n\npackage m\n",
            "modern_wins.go": "//go:build linux\n// +build ignore\n\npackage m\n",
        },
    )
    ok = lambda rel: reads_as_source(tmp_path / rel, GO, tmp_path)  # noqa: E731
    assert not ok("never.go") and not ok("legacy.go")
    assert ok("not_ignored.go") and ok("either.go") and ok("options.go") and ok("modern_wins.go")


@pytest.mark.parametrize(
    "name, constrained",
    [
        ("x_linux.go", True),
        ("x_linux_test.go", True),
        ("x_linux_arm64.go", True),
        ("x_arm64_test.go", True),
        ("x_foo_amd64.go", True),
        ("linux.go", False),
        ("x.go", False),
        ("x_test.go", False),
        ("linux_x.go", False),
    ],
)
def test_a_file_name_that_says_a_goos_or_goarch_is_built_on_some_platforms_only(name, constrained):
    assert platform_file(name) is constrained
