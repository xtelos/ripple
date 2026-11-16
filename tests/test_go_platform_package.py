"""A call in a constrained file is only in the builds that have the file, so a type whose package cannot build there is no candidate.

The compiler cannot answer this by a probe: the oracle stops at the package error ("build constraints exclude all Go files in
c") on the platform where `c` does not build, so no committed expected.json can show the class. These are unit tests, held to
the platform facts stated in each case (`GOOS=linux go build ./b` fails, `GOOS=windows go build ./b` builds).
"""

from pathlib import Path

import pytest
from test_go_probes import HAS_GO, ripple_in

pytestmark = pytest.mark.skipif(not HAS_GO, reason="Go support is not installed (pip install 'ripple-code[go]')")

BASE = {
    "go.mod": "module example.com/p\n\ngo 1.22\n",
    "a.go": "package a\n\ntype I interface{ M() }\n",
    "b/b.go": 'package b\n\nimport "example.com/p/c"\n\ntype T struct{}\n\nfunc (T) M() { _ = c.X }\n',
    "c/c_windows.go": "package c\n\nvar X = 1\n",
}
USE = "package a\n\nfunc Use(i I) {\n\ti.M()\n}\n"


def edges(root: Path, files: dict[str, str]) -> set[str]:
    for rel, text in {**BASE, **files}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _, resolver, _ = ripple_in(root)
    return {e.callee for e in resolver.implement_edges}


def test_a_call_in_a_linux_file_does_not_get_a_type_whose_package_imports_a_windows_only_package(tmp_path):
    # GOOS=linux go build ./b: build constraints exclude all Go files in c; the call exists only on linux, b only off linux
    assert edges(tmp_path, {"use_linux.go": USE}) == set()


def test_a_call_in_a_file_with_a_build_constraint_is_held_to_the_same_rule(tmp_path):
    assert edges(tmp_path, {"use.go": "//go:build linux && amd64\n\n" + USE}) == set()


def test_a_call_in_a_constrained_test_file_is_held_to_the_same_rule(tmp_path):
    assert edges(tmp_path, {"use_linux_test.go": USE}) == set()


def test_a_call_in_a_file_every_build_has_still_gets_it_the_windows_build_has_both(tmp_path):
    assert edges(tmp_path, {"use.go": USE}) == {"example.com/p/b:T.M"}


def test_a_package_that_builds_everywhere_is_a_candidate_for_a_call_in_a_linux_file(tmp_path):
    assert edges(tmp_path, {"use_linux.go": USE, "c/c.go": "package c\n\nvar Y = 1\n"}) == {"example.com/p/b:T.M"}


def test_a_call_in_a_windows_file_gets_the_type_whose_package_builds_on_windows(tmp_path):
    # GOOS=windows go build ./b builds: the call and the type are both in that build
    assert edges(tmp_path, {"use_windows.go": USE}) == {"example.com/p/b:T.M"}


def test_a_package_split_into_a_file_for_linux_and_one_for_the_rest_builds_everywhere(tmp_path):
    split = {
        "use_linux.go": USE,
        "c/c_windows.go": "package c\n\nvar X = 1\n",
        "c/c_linux.go": "package c\n\nvar Y = 1\n",
        "c/c_other.go": "//go:build !linux && !windows\n\npackage c\n\nvar Z = 1\n",
    }
    assert edges(tmp_path, split) == {"example.com/p/b:T.M"}


def test_a_call_in_a_file_that_needs_a_custom_tag_is_in_the_builds_that_set_it(tmp_path):
    # `go build -tags special` on windows builds the call, b and c
    assert edges(tmp_path, {"use.go": "//go:build special\n\n" + USE}) == {"example.com/p/b:T.M"}
    # but a file that needs the tag and linux has no build with c
    assert edges(tmp_path / "linux", {"use.go": "//go:build special && linux\n\n" + USE}) == set()


def test_the_rule_follows_the_imports_of_the_packages_it_imports(tmp_path):
    deep = {
        "use_linux.go": USE,
        "b/b.go": 'package b\n\nimport "example.com/p/mid"\n\ntype T struct{}\n\nfunc (T) M() { mid.F() }\n',
        "mid/m.go": 'package mid\n\nimport "example.com/p/c"\n\nfunc F() { _ = c.X }\n',
    }
    assert edges(tmp_path, deep) == set()
