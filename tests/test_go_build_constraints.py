"""Build constraints, read as go/build reads them: a `//go:build` line, old-style `// +build` lines, the leading-comment rule,
the file-name suffixes and the files the go tool ignores. The verdicts in HEADERS are `go list`'s own (GOOS=linux
GOARCH=amd64, Go 1.27), one file for each header."""

import re

import pytest

from ripple.go_indexer import (
    build_constraint,
    build_header,
    is_constrained,
    is_tests_only,
    never_built,
    satisfiable,
)

LINUX = {"linux", "amd64", "unix", "gc"}
# header -> (does go read a constraint in it, is the file built on linux/amd64). The second column is the go tool's own
# verdict (`go list -f '{{.GoFiles}}|{{.IgnoredGoFiles}}'` over one file for each header), the first follows from it where
# a file that is built on linux has no constraint.
HEADERS = {
    "// +build\n\npackage a\n": (True, False),
    "// +build !!linux\n\npackage a\n": (True, False),
    "// +build lin-ux\n\npackage a\n": (True, False),
    "// +build linux,\n\npackage a\n": (True, False),
    "//go:build linux\n// +build windows\n\npackage a\n": (True, True),
    "// +build windows\npackage a\n": (False, True),
    "// +build windows\n\n/* c */\npackage a\n": (True, False),
    "// +build   windows   darwin   \n\npackage a\n": (True, False),
    "//+build windows\n\npackage a\n": (True, False),
    "// +buildwindows\n\npackage a\n": (False, True),
    "/* x */\n// +build windows\n\npackage a\n": (False, True),
    "// doc\n// +build windows\n\npackage a\n": (True, False),
    "// +build windows\n// +build linux\n\npackage a\n": (True, False),
    "// +build windows linux\n// +build !linux\n\npackage a\n": (True, False),
    "package a\n// +build windows\n": (False, True),
    "// +build !windows,!linux\n\npackage a\n": (True, False),
    "//go:build windows\n\npackage a\n": (True, False),
    "//go:build windows\npackage a\n": (True, False),
    "/* x */\n//go:build windows\n\npackage a\n": (True, False),
    "// +build windows\n\n// doc\n\npackage a\n": (True, False),
    "// +build linux !windows\n\npackage a\n": (True, True),
    "// +build \tlinux\n\npackage a\n": (True, True),
    "\n\n// +build windows\n\npackage a\n": (True, False),
    "// +build !linux,windows\n// +build linux\n\npackage a\n": (True, False),
}


def holds(expression: str) -> bool:
    """A `//go:build` expression on linux/amd64 (the tags in LINUX), evaluated independently of satisfiable()."""
    tokens = re.findall(r"&&|\|\||!|\(|\)|[\w.]+", expression)
    at = 0

    def either():
        nonlocal at
        value = both()
        while at < len(tokens) and tokens[at] == "||":
            at += 1
            value = both() or value
        return value

    def both():
        nonlocal at
        value = unary()
        while at < len(tokens) and tokens[at] == "&&":
            at += 1
            value = unary() and value
        return value

    def unary():
        nonlocal at
        token = tokens[at]
        at += 1
        if token == "!":
            return not unary()
        if token == "(":
            value = either()
            at += 1
            return value
        return token in LINUX

    return either()


@pytest.mark.parametrize("header", sorted(HEADERS))
def test_a_header_is_a_constraint_when_go_reads_one_and_holds_when_go_builds_the_file(header):
    reads, built = HEADERS[header]
    constraint = build_constraint(header.encode())
    assert bool(constraint) == reads
    assert is_constrained("x.go", header.encode()) == reads
    assert (holds(constraint) if constraint else True) == built


@pytest.mark.parametrize(
    ("header", "never"),
    [
        ("// +build ignore\n\npackage a\n", True),
        ("// +build ignore,linux\n\npackage a\n", True),
        ("// +build ignore linux\n\npackage a\n", False),
        ("// +build linux\n// +build ignore\n\npackage a\n", True),
        ("//go:build ignore && linux\n\npackage a\n", True),
        ("//go:build ignore || linux\n\npackage a\n", False),
        ("// +build !ignore\n\npackage a\n", False),
        ("// +build\n\npackage a\n", True),
        ("package a\n", False),
    ],
)
def test_a_file_no_build_includes_is_one_whose_constraint_needs_the_tag_ignore(header, never):
    assert never_built(header.encode()) is never


def test_a_test_file_is_constrained_and_a_plain_one_is_not_tests_only_when_a_constraint_is_there():
    plain = b"package a\n"
    old = b"// +build linux\n\npackage a\n"
    assert is_constrained("a_test.go", plain) and is_tests_only("a_test.go", plain)
    assert is_constrained("a_test.go", old) and not is_tests_only("a_test.go", old)
    assert is_constrained("a_linux.go", plain) and is_constrained("a_linux_amd64.go", plain) and is_constrained("a_linux_test.go", plain)
    assert not is_constrained("linux.go", plain) and not is_constrained("a_linuxx.go", plain) and not is_constrained("a.go", plain)
    assert not is_tests_only("a_linux_test.go", plain)


def test_the_header_is_read_as_go_build_reads_it():
    assert build_header(b"// c\n//go:build linux\n\npackage a\n")[0] == b"//go:build linux"
    assert build_header(b"//go:buildlinux\n\npackage a\n")[0] is None
    assert build_header(b"package a\n//go:build linux\n")[0] is None
    assert build_header(b"/* a\n//go:build linux\n*/\npackage a\n")[0] is None
    assert build_header(b"// +build linux\n\n// +build amd64\n\npackage a\n")[1].count(b"// +build amd64") == 1


def test_satisfiable_reads_the_same_expression_syntax_the_header_makes():
    assert satisfiable("linux && amd64") and not satisfiable("ignore && linux") and satisfiable("ignore || linux")
