"""The table that holds ripple's reading of which Go files a build has to go/build's own: `go list` over the cross product of
header forms and file-name forms, for several GOOS/GOARCH pairs and a tag set.

    GO=/path/to/go python bench/go_oracle/go_build_table.py        (writes bench/go_oracle/go_build_table.json)

Every case is one file: a name (a platform suffix, a second dot, `_test`, ...) in a directory whose files all carry one header (a
`//go:build` line, an old-style `// +build` line, a byte order mark, no-break spaces, a vertical tab, a header after the package
clause, ...) and a `package p` clause. For each platform `go list -e` says whether the go tool puts the file in GoFiles (B),
TestGoFiles (T), IgnoredGoFiles (I) or InvalidGoFiles (E, a header go refuses), or does not see it (-: a name that starts with `_`
or `.`). tests/test_go_build_table.py holds ripple.go_indexer.file_in_build, and the platform-free predicates built on the same
reading (is_constrained, never_built, is_tests_only), to that table; it needs no Go toolchain. The table also records go's own
lists of known operating systems and architectures, which the test compares to ripple's.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE = HERE / "go_build_table.json"

NBSP, EMSPACE, NEL = " ".encode(), " ".encode(), "\u0085".encode()
BOM = b"\xef\xbb\xbf"


def gb(expression: str, after: str = "\n\n") -> bytes:
    return f"//go:build {expression}{after}".encode()


# (label, what comes before `package p`, what comes after it)
HEADERS: list[tuple[str, bytes, bytes]] = [
    ("no header", b"", b""),
    ("go:build linux", gb("linux"), b""),
    ("go:build !linux", gb("!linux"), b""),
    ("go:build linux && amd64", gb("linux && amd64"), b""),
    ("go:build linux || darwin", gb("linux || darwin"), b""),
    ("go:build (linux || darwin) && !arm64", gb("(linux || darwin) && !arm64"), b""),
    ("go:build unix", gb("unix"), b""),
    ("go:build !unix", gb("!unix"), b""),
    ("go:build ignore", gb("ignore"), b""),
    ("go:build cgo", gb("cgo"), b""),
    ("go:build !cgo", gb("!cgo"), b""),
    ("go:build gc", gb("gc"), b""),
    ("go:build gccgo", gb("gccgo"), b""),
    ("go:build go1.18", gb("go1.18"), b""),
    ("go:build go1.99", gb("go1.99"), b""),
    ("go:build foo", gb("foo"), b""),
    ("go:build foo || windows", gb("foo || windows"), b""),
    ("go:build 386", gb("386"), b""),
    ("go:build ñ (a letter)", gb("ñ"), b""),
    ("go:build ½ (not a letter or digit)", gb("½"), b""),
    ("go:build linux,amd64 (comma)", gb("linux,amd64"), b""),
    ("go:build !!linux", gb("!!linux"), b""),
    ("go:build (empty)", b"//go:build\n\n", b""),
    ("go:build linux // comment", gb("linux // c"), b""),
    ("go:build linux then +build darwin", b"//go:build linux\n// +build darwin\n\n", b""),
    ("two go:build lines", b"//go:build linux\n\n//go:build darwin\n\n", b""),
    ("go:build<tab>linux", b"//go:build\tlinux\n\n", b""),
    ("BOM go:build linux", BOM + gb("linux"), b""),
    ("BOM +build linux", BOM + b"// +build linux\n\n", b""),
    ("BOM and no constraint", BOM + b"// a comment\n\n", b""),
    ("NBSP then go:build linux", NBSP + gb("linux"), b""),
    ("go:build linux then NBSP", b"//go:build linux" + NBSP + b"\n\n", b""),
    ("go:build NBSP linux", b"//go:build" + NBSP + b"linux\n\n", b""),
    ("go:build VT linux", b"//go:build\vlinux\n\n", b""),
    ("go:build FF linux", b"//go:build\flinux\n\n", b""),
    ("go:build linux CRLF", b"//go:build linux\r\n\r\n", b""),
    ("go:buildlinux (no space)", b"//go:buildlinux\n\n", b""),
    ("go:build EM SPACE linux", b"//go:build" + EMSPACE + b"linux\n\n", b""),
    ("NEL then go:build linux", NEL + gb("linux"), b""),
    ("go:build linux&&amd64", gb("linux&&amd64"), b""),
    ("go:build double spaces", gb("  linux  &&  amd64  "), b""),
    ("go:build ((linux))", gb("((linux))"), b""),
    ("go:build !(linux || darwin)", gb("!(linux || darwin)"), b""),
    ("go:build linux ||", gb("linux ||"), b""),
    ("go:build (linux", gb("(linux"), b""),
    ("go:build linux)", gb("linux)"), b""),
    ("go:build 1.5", gb("1.5"), b""),
    ("+build linux", b"// +build linux\n\n", b""),
    ("+build (no space after //)", b"//+build linux\n\n", b""),
    ("+build linux amd64", b"// +build linux amd64\n\n", b""),
    ("+build linux,amd64", b"// +build linux,amd64\n\n", b""),
    ("+build !linux", b"// +build !linux\n\n", b""),
    ("+build ignore", b"// +build ignore\n\n", b""),
    ("+build (empty)", b"// +build\n\n", b""),
    ("+build linux, no blank line after", b"// +build linux\n", b""),
    ("+build linux then +build amd64", b"// +build linux\n\n// +build amd64\n\n", b""),
    ("+build linux then +build !linux", b"// +build linux\n// +build !linux\n\n", b""),
    ("+build a½", "// +build a½\n\n".encode(), b""),
    ("+build !!linux", b"// +build !!linux\n\n", b""),
    ("+build lin-ux", b"// +build lin-ux\n\n", b""),
    ("+build linux NBSP", b"// +build linux" + NBSP + b"\n\n", b""),
    ("NBSP then +build linux", NBSP + b"// +build linux\n\n", b""),
    ("+build VT linux", b"// +build\vlinux\n\n", b""),
    ("+build linux !windows", b"// +build linux !windows\n\n", b""),
    ("+build 102 operands joined by commas (too complex, go drops the line)", b"// +build " + b",".join([b"windows"] * 102) + b"\n\n", b""),
    ("+build 101 operands joined by commas (still read)", b"// +build " + b",".join([b"windows"] * 101) + b"\n\n", b""),
    ("block comment then go:build", b"/* c */\n" + gb("linux"), b""),
    ("go:build inside a block comment", b"/*\n//go:build linux\n*/\n\n", b""),
    ("go:build after a block comment on the line", b"/* c */ //go:build linux\n\n", b""),
    ("comment then go:build", b"// c\n" + gb("linux"), b""),
    ("block comment then +build", b"/* c */\n// +build linux\n\n", b""),
    ("go:build after the package clause", b"", b"\n//go:build linux\n"),
    ("+build after the package clause", b"", b"\n// +build linux\n"),
    ("go:build linux then +build in the doc comment", gb("linux", "\n") + b"// Package p.\n", b""),
]

NAMES = [
    "a.go", "a_test.go", "a_linux.go", "a_linux_test.go", "a_amd64.go", "a_arm64_test.go", "a_linux_amd64.go", "a_windows_amd64.go",
    "a_darwin_arm64_test.go", "a_amd64_linux.go", "a_linux.pb.go", "a.pb_linux.go", "a_linux.pb_test.go", "a_linux.x.y.go",
    "a.linux.go", "linux.go", "linux_amd64.go", "a_unix.go", "a_android.go", "a_ios.go", "a_illumos.go", "a_solaris.go",
    "a_js_wasm.go", "a_wasm.go", "a_386.go", "a_Linux.go", "a_linux_x.go", "a_x_linux.go", "a_test_test.go", "a__linux.go",
    "a_linux_.go", "a_test_linux.go", "a_linux_amd64_test.go", "a_x_amd64_test.go", "_a_linux.go", ".a.go",
]

PLATFORMS = [
    ("linux", "amd64", ""), ("linux", "arm64", ""), ("windows", "amd64", ""), ("darwin", "arm64", ""), ("android", "arm64", ""),
    ("ios", "arm64", ""), ("illumos", "amd64", ""), ("js", "wasm", ""), ("freebsd", "386", ""), ("plan9", "arm", ""),
    ("wasip1", "wasm", ""), ("linux", "amd64", "foo"),
]


def content(header: tuple[str, bytes, bytes]) -> bytes:
    return header[1] + b"package p\n" + header[2]


def go_lists(go: str) -> dict[str, list[str]]:
    source = Path(subprocess.run([go, "env", "GOROOT"], capture_output=True, text=True, check=True).stdout.strip()) / "src" / "internal" / "syslist" / "syslist.go"
    text = source.read_text()

    def block(name: str) -> list[str]:
        body = re.search(r"var " + name + r" = map\[string\]bool\{(.*?)\n\}", text, re.S).group(1)
        return sorted(re.findall(r'"(\w+)":\s+true', body))

    return {"known_os": block("KnownOS"), "unix_os": block("UnixOS"), "known_arch": block("KnownArch")}


def table(go: str) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="gobuild-table-"))
    try:
        (scratch / "go.mod").write_text("module example.com/tbl\n\ngo 1.22\n")
        for index, header in enumerate(HEADERS):
            directory = scratch / f"h{index:03d}"
            directory.mkdir()
            for name in NAMES:
                (directory / name).write_bytes(content(header))
        verdicts = [[""] * len(NAMES) for _ in HEADERS]
        fmt = "{{.ImportPath}}|{{.GoFiles}}|{{.TestGoFiles}}|{{.XTestGoFiles}}|{{.IgnoredGoFiles}}|{{.InvalidGoFiles}}"
        for goos, goarch, tags in PLATFORMS:
            env = {**os.environ, "GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0", "GOFLAGS": "-buildvcs=false", "GOPROXY": "off", "GOTOOLCHAIN": "local"}
            command = [go, "list", "-e", "-f", fmt] + (["-tags", tags] if tags else []) + [f"./h{index:03d}" for index in range(len(HEADERS))]
            done = subprocess.run(command, cwd=scratch, env=env, capture_output=True, text=True)
            if done.returncode:
                sys.exit(f"go list failed for {goos}/{goarch} {tags}:\n{done.stderr[-2000:]}")
            for line in done.stdout.splitlines():
                path, *lists = line.split("|")
                index = int(path.rsplit("/h", 1)[1])
                built, tests, external, ignored, invalid = ([n for n in lst.strip("[]").split()] for lst in lists)
                for position, name in enumerate(NAMES):
                    verdict = "B" if name in built else "T" if name in tests or name in external else "I" if name in ignored else "E" if name in invalid else "-"
                    verdicts[index][position] += verdict
        version = subprocess.run([go, "version"], capture_output=True, text=True, check=True).stdout.strip()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {
        "go": version,
        "platforms": [list(p) for p in PLATFORMS],
        **go_lists(go),
        "names": NAMES,
        "headers": [{"label": label, "before": before.hex(), "after": after.hex()} for label, before, after in HEADERS],
        "verdicts": verdicts,
    }


def dump(data: dict) -> str:
    """One line per header, so a diff reads as the forms that changed."""
    lines = ["{"]
    for key in ("go", "platforms", "known_os", "unix_os", "known_arch", "names"):
        lines.append(f" {json.dumps(key)}: {json.dumps(data[key])},")
    lines.append(' "headers": [')
    lines += ["  " + json.dumps(h) + ("," if i < len(data["headers"]) - 1 else "") for i, h in enumerate(data["headers"])]
    lines.append(" ],")
    lines.append(' "verdicts": [')
    lines += ["  " + json.dumps(v) + ("," if i < len(data["verdicts"]) - 1 else "") for i, v in enumerate(data["verdicts"])]
    return "\n".join(lines + [" ]", "}"]) + "\n"


def main() -> None:
    go = os.environ.get("GO") or shutil.which("go")
    if not go:
        sys.exit("go_build_table.py needs a Go toolchain: put go on PATH or set GO")
    TABLE.write_text(dump(table(go)))
    print("wrote", TABLE)


if __name__ == "__main__":
    main()
