"""`check` for Rust: run `cargo test` and read the verdict from what cargo and libtest print.

Stable cargo has no JSON test output (libtest's `--format json` is refused on a stable compiler: "only
accepted on the nightly compiler with -Z unstable-options"), so the verdict is read from the two things that
are stable and structured enough, and is never one of them alone:

- `--message-format json`, which puts one JSON object per line on cargo's stdout: a `compiler-artifact` for
  each test binary built (its executable, target and package), `compiler-message` for each compiler error and
  `build-finished` with whether the build succeeded;
- libtest's own default output, which cargo's `Running ...` and `Doc-tests ...` lines divide into one section
  per test binary: `running N tests`, one `test NAME ... ok|FAILED|ignored` line per test and a closing
  `test result: ...` line. Its counts are checked against its own lines, and every binary the JSON says was
  built must have a section with a result. A section can hold SEVERAL such runs (rustdoc on edition 2024 runs
  the doc tests it cannot merge as a second run under the same `Doc-tests` line): each run is judged by its own
  lines, a run with no result line is a problem, and the section is the sum of its runs.

The output is read in the order it was written (stdout and stderr merged), which is the only way to know which
binary a line belongs to. cargo's exit status is a third witness: it can only add a problem to a pass, never
take one away. These cases come out as what they are, never as "passed", each with a captured run under
tests/fixtures/rs_check/captures:

- a build failure (build-finished false, or no build-finished at all: a manifest cargo cannot read), including a
  test target that does not build and a build script that fails,
- a filter that matches nothing (`running 0 tests`, exit 0): a test asked for by name needs a result line of its
  own, in the target it was asked for,
- an ignored test that was asked for,
- a test that panics outside libtest (a `harness = false` target), a doctest failure,
- a test binary that exits early (`std::process::exit(0)` in a test: exit 0, and no result line), aborts or
  is killed, and ripple's own timeout.

What is run is exactly what was asked, and where it is narrower or broader the verdict says so. An id, a file or a directory is first
mapped, from the sources, to the crate and the module path libtest calls it by; WHAT THAT HOLDS is never read out of the sources, because
a test a macro or an attribute macro writes (`case!(..)`, `#[rstest]`, `#[test_case]`) is no `#[test]` function in any file. The test
binary is asked (`cargo test <target> -- --list`, stable; it builds the binary once and the run after it finds it built), and the run is
exactly the listed tests under that module path, with `-- --exact`, one `cargo test` per package and target (`-p PACKAGE` and `--lib`,
`--bin NAME`, `--test NAME` or `--example NAME`), because one filter applies to every binary of a run and a name is not unique across
packages and targets. cargo's default filter is a substring match (`adds` would also run `adds_zero`). A name that is not listed is "no test of
that name exists", never a filter that quietly matched nothing. What a path cannot be mapped to a module for with certainty (`#[path]`, a file the
crate declares twice, `include!`, a macro that may declare a module) is the whole target, and the verdict says so. The list is also a
witness against a binary that imitates libtest: the result line's `filtered out` has to be what the list leaves over.

What remains, said plainly: stable cargo gives no structured test results, so wherever the run is the whole of a binary and has no list
(no arguments, a package, a whole target: an integration test file, a crate root, a directory that holds one, an example, and every
fallback to the whole target) a test binary that deliberately writes libtest's lines itself and exits with status 0 can be read as
passed. A forgery is caught only where the run is narrower than the binary's list.

This is a runner for checker.py: `run(repo, tests, timeout)` returns the same keys as the generic check
(passed, exit_code, timed_out, duration_s, command, summary, failures, failure_count) plus `problems` (why a run
is not a clean pass), `counts` and `notes`. checker.RUNNERS names it.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .checker import MAX_COMMAND, MAX_FAILURES, check_tests, clip, run_process

# What `cargo test` is run with: no colour whatever the environment says, JSON events on stdout, and every
# test binary run even when an earlier one failed (the verdict is of the whole selection, not of the first failure).
# `--config` outranks the environment and every config file: CARGO_TERM_QUIET drops the `Running` lines the output is divided by and
# CARGO_TERM_VERBOSE rewrites them (both measured), so neither may reach the parse.
BASE_ARGV = [
    "cargo", "test", "--color", "never", "--config", "term.quiet=false", "--config", "term.verbose=false",
    "--message-format", "json", "--no-fail-fast",
]  # fmt: skip
METADATA_ARGV = ["cargo", "metadata", "--no-deps", "--format-version", "1", "--config", "term.quiet=false", "--config", "term.verbose=false"]
MAX_PROBLEMS = 10
LIB_KINDS = frozenset({"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"})
IDENT = re.compile(r"^(?:r#)?[^\W\d]\w*$")
ID = re.compile(r"^(?P<module>[^:\s]+):(?P<name>(?:r#)?[^\W\d]\w*(?:::(?:r#)?[^\W\d]\w*)*)$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RUNNING = re.compile(r"^ {5}Running (?P<what>.+?) \((?P<exe>.*)\)\s*$")
DOC_TESTS = re.compile(r"^ {3}Doc-tests (?P<crate>.+?)\s*$")
RUNNING_N = re.compile(r"^running (?P<n>\d+) tests?$")
# libtest 1.99 writes a test as `test NAME[ - DECORATION] ... STATUS`. Measured with real runs (tests/fixtures/rs_check/captures/decorated.txt):
# the decoration is ` - should panic` (a `#[should_panic]` test, with or without `expected`; an ignored one has none) and, for a doc test,
# ` - compile` (`no_run`), ` - compile fail` or ` - should panic`; the status is `ok`, `FAILED`, `ignored` or `ignored, REASON`. A bench line
# (`bench: N ns/iter`) and the time reports of `--report-time` need a nightly compiler, so a stable run has neither; a test line that is
# none of these is a problem naming the line, never a name that silently matches nothing.
DECORATIONS = ("should panic", "compile fail", "compile")
TEST_LINE = re.compile(
    r"^test (?P<name>.+?)(?: - (?P<decoration>" + "|".join(DECORATIONS) + r"))? \.\.\. (?P<status>ok|FAILED|ignored)(?:, .*)?$"
)
TEST_LOOKING = re.compile(r"^test .+ \.\.\. ")
# a unit test is a path with no space in it; a doc test is `FILE - ITEM (line N)`. A name that is neither has a decoration ripple does not know.
TEST_NAME = re.compile(r"\S+|.+ - .+ \(line \d+\)")
LIST_LINE = re.compile(r"^(?P<name>.+): (?P<kind>test|benchmark)$")
LIST_TOTAL = re.compile(r"^(?P<tests>\d+) tests?, (?P<benchmarks>\d+) benchmarks?$")
RESULT = re.compile(
    r"^test result: (?P<verdict>ok|FAILED)\. (?P<passed>\d+) passed; (?P<failed>\d+) failed; (?P<ignored>\d+) ignored; "
    r"(?P<measured>\d+) measured; (?P<filtered>\d+) filtered out"
)
FAILURE_BLOCK = re.compile(r"^---- (?P<name>.+) stdout ----$")
DIED = re.compile(r"process didn't exit successfully: .* \((?P<how>[^()]*)\)\s*$")
PANICKED = re.compile(r"panicked at (?P<where>[^\s:]+:\d+:\d+):?$")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _within(path: str, parent: str) -> bool:
    """Whether the repo-relative `path` is `parent` or below it ("" is the repo)."""
    return parent == "" or path == parent or path.startswith(parent + "/")


def _relative(repo: Path, path: str | Path) -> str | None:
    """The repo-relative posix path of an absolute one ("" for the repo itself), None when it is outside the repo."""
    try:
        rel = Path(os.path.realpath(path)).relative_to(os.path.realpath(repo)).as_posix()
    except ValueError:
        return None
    return "" if rel == "." else rel


# ---- the layout: what cargo says the workspaces, packages and targets are --------------------------


@dataclass(frozen=True)
class Target:
    package: str
    name: str  # as cargo names it (`my-tool`, not `my_tool`): what `--bin` and `--test` take
    kind: str  # lib | bin | test | example | bench | build
    root: str  # repo-relative crate root file
    test: bool  # whether `cargo test` builds a test binary for it (`test = false` in the manifest says no)
    required: tuple[str, ...]  # required-features: the target is skipped, silently, unless every one is on


@dataclass
class PackageInfo:
    name: str
    dir: str  # repo-relative, "" for the repo root
    manifest: str
    targets: list[Target]
    features: dict[str, list[str]]

    def enabled(self) -> set[str]:
        """The features a plain `cargo test` has on: `default` and what it enables."""
        seen: set[str] = set()
        todo = ["default"] if "default" in self.features else []
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            todo += [f for f in self.features.get(name, []) if isinstance(f, str) and f in self.features]
        return seen


@dataclass
class Space:
    """One cargo workspace (a lone package is a workspace of one)."""

    root: str | None  # repo-relative directory of its Cargo.toml, None when that is outside the repo
    packages: list[PackageInfo]
    nested: bool = False  # inside another workspace's directory without being a member of it (its `exclude`): run when asked for, not by default


def _kind(raw: list) -> str:
    first = raw[0] if raw else ""
    return "lib" if first in LIB_KINDS else {"custom-build": "build"}.get(first, first)


def parse_metadata(text: str, repo: Path) -> Space:
    """The workspace a `cargo metadata --no-deps` answer describes. Raises ValueError for text that is not one."""
    try:
        data = json.loads(text)
        members = set(data["workspace_members"])
        packages = []
        for entry in data["packages"]:
            if entry["id"] not in members:
                continue
            manifest = _relative(repo, entry["manifest_path"])
            pdir = _relative(repo, Path(entry["manifest_path"]).parent)
            if manifest is None or pdir is None:
                continue  # a member outside the repo: nothing there is ripple's to name
            targets = []
            for raw in entry["targets"]:
                root = _relative(repo, raw["src_path"])
                if root is None:
                    continue
                required = raw.get("required-features") or []
                targets.append(Target(entry["name"], raw["name"], _kind(raw["kind"]), root, bool(raw.get("test", True)), tuple(required)))
            packages.append(PackageInfo(entry["name"], pdir, manifest, targets, entry.get("features") or {}))
        return Space(_relative(repo, data["workspace_root"]), packages)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"`cargo metadata` printed something ripple cannot read ({type(error).__name__}: {error})") from error


def manifests_of(repo: Path, files: list[Path]) -> list[str]:
    """The Cargo.toml files of the repo (those at or above a Rust source file), shallowest first."""
    from .rs_modules import manifests

    return sorted(manifests(repo, files), key=lambda m: (m.count("/"), m))


# ---- reading the sources: which crate and module path a file is -----------------------------------


class Model:
    """ripple's own reading of the repo's crates (rs_modules), used to turn a file and a test name into the
    crate that holds it and the name libtest gives the test. Loaded when first needed, tree by tree."""

    def __init__(self, repo: Path):
        self.repo = repo
        self._files: list[Path] | None = None
        self._workspace = None
        self._trees: dict[str, object] = {}
        self._parse = None

    @property
    def files(self) -> list[Path]:
        if self._files is None:
            from .indexer import walk_languages
            from .profile import RUST

            self._files = walk_languages(self.repo, [RUST])[0]["rust"]
        return self._files

    @property
    def workspace(self):
        if self._workspace is None:
            from . import rs_modules

            try:
                self._workspace = rs_modules.load_workspace(self.repo, self.files)
            except ImportError as error:
                raise ValueError(str(error)) from error
        return self._workspace

    def package_of(self, rel: str):
        """The package a repo-relative path is in: the deepest one whose directory holds it."""
        holders = [p for p in self.workspace.packages.values() if _within(rel, p.dir)]
        return max(holders, key=lambda p: len(p.dir)) if holders else None

    def tree(self, crate):
        key = f"{crate.kind} {crate.root}"
        if key not in self._trees:
            from . import rs_modules

            self._trees[key] = rs_modules.build_tree(self.repo, crate, {p.relative_to(self.repo).as_posix() for p in self.files}, self._parser())
        return self._trees[key]

    def _parser(self):
        if self._parse is None:
            from .rs_indexer import RustNotInstalled, blank_comments, load_parser

            try:
                parser = load_parser()
            except RustNotInstalled as error:
                raise ValueError(str(error)) from error
            cache: dict[str, tuple | None] = {}

            def parse(rel: str):
                if rel not in cache:
                    try:
                        src = (self.repo / rel).read_bytes()
                        src, tree = blank_comments(parser, src, parser.parse(src))
                        cache[rel] = (src, tree.root_node)
                    except Exception:  # noqa: BLE001 (a file the parser cannot read is a file nothing is found in)
                        cache[rel] = None
                return cache[rel]

            self._parse = parse
        return self._parse

    def modules_in(self, package, within: str) -> list:
        """Every module of the package's crates that is read from a file at or below `within`."""
        found = []
        for crate in self.workspace.crates:
            if crate.package is package:
                found += [m for m in self.tree(crate).modules if _within(m.file, within)]
        return found

    def locate(self, root: str, name: str) -> tuple[str, str] | None:
        """(file, name inside the file) of the test libtest calls `name` in the crate rooted at `root`, None when unknown.

        The file is the one of the longest module path the name starts with, so a test a macro puts in a module of
        its own (`tests::case_1`) is still placed in the file of `tests`. libtest writes a raw identifier as it is
        written (`r#type::tests::t`) and a module's path is read without the `r#`, so they are compared bare; the name
        returned keeps libtest's spelling, which is what `file.rs::name` takes back.
        """
        parts = name.split("::")
        bare = tuple(_bare(p) for p in parts)
        for crate in self.workspace.crates:
            if crate.root != root:
                continue
            best = None
            for module in self.tree(crate).modules:
                depth = len(module.path)
                if depth < len(parts) and module.path == bare[:depth] and (best is None or depth > len(best.path)):
                    best = module
            if best is not None:
                return best.file, "::".join(parts[max(len(best.path) - len(best.inline), 0) :])
        return None


def _attribute_names(src: bytes, item) -> list[str]:
    from .rs_modules import _attributes, _read

    names = []
    for attribute in _attributes(item):
        inner = attribute.named_children[0] if attribute.named_children else None
        if inner is not None and inner.named_children:
            names.append(_read(src, inner.named_children[0]))
    return names


def declared_functions(module) -> list[tuple[str, bool, bool]]:
    """(name, whether it carries a test attribute, whether it carries any attribute) of each function written directly in the module's body.

    A test attribute is one whose path ends in `test` (`#[test]`, `#[tokio::test]`). Other test macros
    (`rstest`, `proptest!`) are not seen; a test asked for by name still runs, and is judged by what cargo reports.
    """
    from .rs_modules import _name

    found = []
    for item in module.node.named_children:
        if item.type == "function_item":
            attributes = _attribute_names(module.src, item)
            marked = any(a.rsplit("::", 1)[-1].strip() == "test" for a in attributes)
            found.append((_name(module.src, item.child_by_field_name("name")), marked, bool(attributes)))
    return found


# ---- the request: what the caller asked to run ----------------------------------------------------


@dataclass(frozen=True)
class Pick:
    """One crate's share of a request. What it covers is never read out of the source: the source maps a path to a crate and a module
    path, and the test binary (`-- --list`) says which tests are there.

    names: exact ids (libtest paths as written in the source, `tests::adds`), each checked against the list;
    prefixes: module paths (a file or directory), every listed test below one of them;
    neither: the whole target.
    """

    given: str
    root: str  # repo-relative crate root file, with `kind` it is the target
    kind: str  # lib | bin | test | example (ripple's names)
    names: tuple[str, ...] | None = None
    prefixes: tuple[tuple[str, ...], ...] = ()

    @property
    def whole(self) -> bool:
        return self.names is None and not self.prefixes


@dataclass
class Request:
    picks: list[Pick] = field(default_factory=list)
    packages: list[tuple[str, str]] = field(default_factory=list)  # (given, package directory): the whole package
    notes: list[str] = field(default_factory=list)


def normalize(repo: Path, tests: list[str]) -> list[str]:
    """The tests with each Rust symbol id as `impact` hands it out turned into the `file.rs::name` form.

    `impact` lists a Rust test function among the callers to update (a Rust repo has no collected tests, so
    its `tests` list is empty) as `<file path without .rs>:<name inside the file>`, `src/lib:tests::adds`, a
    `mod.rs` folded into its directory (`src/net:tests::t_net`). That id names no path, so it is turned into
    the file it is in: `src/lib.rs::tests::adds`. One that finds no file is left as it was, for the checks
    that follow to refuse.
    """
    root = repo.resolve()
    result = []
    for given in tests:
        match = ID.match(given) if isinstance(given, str) else None  # anything else is for check_tests to refuse
        if match is None:
            result.append(given)
            continue
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in given):
            raise ValueError(f"not a Rust test id: {given!r} (control character)")
        module = match["module"]
        parts = module.split("/")
        if module.startswith("/") or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"not a Rust test id: {given!r} (the path must be inside the repo, written without empty, . or .. parts)")
        # each candidate is resolved and held to the repo before it is looked at, so a link out of the repo is not followed
        files = []
        for candidate in (module + ".rs", module + "/mod.rs"):
            resolved = (root / candidate).resolve()
            if resolved.is_relative_to(root) and resolved.is_file():
                files.append(candidate)
        result.append(f"{files[0]}::{match['name']}" if len(files) == 1 else given)
    return result


def parse_wants(repo: Path, tests: list[str], model: Model | None = None) -> Request:
    """The request as crates and test names. Raises ValueError for one `cargo test` cannot be asked for.

    The forms: a directory (every test of the files below it, and every test of a package that is wholly below
    it), a file (the tests it declares; for the root of an integration test, that whole test target) and
    `file.rs::name` (one test, `tests::adds` as it is written in the file). The caller has already run
    checker.check_tests: no option, no `@file`, nothing outside the repo. Nothing here starts a process.
    """
    request = Request()
    model = model or Model(repo)
    for given in tests:
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in given):
            raise ValueError(f"not a Rust test path or id: {given!r} (control character)")
        path_part, sep, name_part = given.partition("::")
        path = (repo / path_part).resolve()
        rel = _relative(repo, path)
        if rel is None:
            raise ValueError(f"{given!r} is not a file or directory inside {repo}")
        if path.is_dir():
            if sep:
                raise ValueError(f"{given!r}: a directory has no test names; name a .rs file")
            _directory(model, request, given, rel)
        elif not path.name.endswith(".rs"):
            raise ValueError(f"{given!r} is not a Rust source file (.rs) or a directory")
        elif not sep:
            _file(model, request, given, rel)
        else:
            parts = name_part.split("::")
            if not all(IDENT.match(p) for p in parts):
                raise ValueError(f"{given!r}: {name_part!r} is not a path of Rust names (file.rs::module::function)")
            _function(model, request, given, rel, tuple(parts))
    return request


def _modules_of_file(model: Model, rel: str, given: str) -> list:
    package = model.package_of(rel)
    if package is None:
        raise ValueError(f"{given!r}: {rel} is in no Cargo package ripple reads")
    modules = model.modules_in(package, rel)
    modules = [m for m in modules if m.file == rel]
    if not modules:
        raise ValueError(f"{given!r}: no crate of the package {package.name} includes {rel} (no `mod` declaration reaches it)")
    return modules


def _kind_pick(given: str, module, names: tuple[str, ...] | None = None, prefixes: tuple[tuple[str, ...], ...] = ()) -> Pick | None:
    crate = module.crate
    if crate.kind in ("bench", "build"):
        return None
    return Pick(given, crate.root, crate.kind, names, prefixes)


def _bare(name: str) -> str:
    """A name as the compiler sees it: `r#match` is `match`."""
    return name[2:] if name.startswith("r#") else name


def _function(model: Model, request: Request, given: str, rel: str, parts: tuple[str, ...]) -> None:
    """`file.rs::a::b::name`: the module `a::b` of the file, and the name in it. Whether the test exists is the binary's to say (it may
    be generated by a macro, which no reading of the file can see); what a source reading is sure of is refused here: a module the file
    does not have, and a function written with no attribute at all (a test is marked `#[test]` or by another attribute macro).

    The refusal of a module is made only where the source is certain (`_opaque`): where a macro, an attribute macro or `include!` may write
    modules, the module path is left to the test binary's list, from the longest part of it the file does have, and a name the list lacks
    is the usual "no test named X exists"."""
    modules = _modules_of_file(model, rel, given)
    bare = tuple(_bare(p) for p in parts)
    holders = [m for m in modules if m.inline == bare[:-1]]
    known = len(bare) - 1  # how much of the module path the file's own source has
    if not holders:
        if not _opaque(model, modules) or not any(not m.inline for m in modules):
            inline = sorted({"::".join(m.inline) for m in modules if m.inline})
            raise ValueError(
                f"{given!r}: {rel} has no module {'::'.join(parts[:-1])!r} (its inline modules: {', '.join(inline) or 'none'}); "
                "pass file.rs::module::function"
            )
        while not holders:
            known -= 1
            holders = [m for m in modules if m.inline == bare[:known]]
    declared = [(n, attributed) for m in holders if known == len(bare) - 1 for n, _, attributed in declared_functions(m) if n == bare[-1]]
    if declared and not any(attributed for _, attributed in declared):
        raise ValueError(f"{given!r}: {'::'.join(parts)} is a function with no attribute, so it is not a test (a test is marked #[test] or another test macro)")
    picks = [pick for m in holders if (pick := _kind_pick(given, m, ("::".join(m.path + parts[known:]),))) is not None]
    if not picks:
        raise ValueError(f"{given!r}: that is in a {holders[0].crate.kind} target, and check does not run those")
    for pick in picks:
        if pick not in request.picks:
            request.picks.append(pick)


# attributes that are not macros and write no module: anything else may be an attribute macro (`#[rstest]` writes a module per function)
INERT_ATTRIBUTES = frozenset(
    "allow warn deny forbid expect cfg cfg_attr test ignore should_panic doc derive inline must_use deprecated macro_use macro_export path "
    "repr non_exhaustive no_mangle automatically_derived rustfmt::skip".split()
)


def _opaque(model: Model, modules: list) -> bool:
    """Whether what a file's modules hold cannot be told from its source: a macro called where items go (a macro may write a module), an
    attribute that is not one of the inert ones (an attribute macro may), an `include!`, or a crate whose modules were not all read."""
    roots = [m for m in modules if not m.inline]
    if any(model.tree(m.crate).problems for m in roots):
        return True
    for module in roots:
        if re.search(rb"\binclude!", module.src):
            return True
        todo = [module.node]
        while todo:
            node = todo.pop()
            if node.type == "macro_invocation":
                up = node.parent  # `m!(..);` as an item is an expression statement, `m! { .. }` is the item itself
                if up is not None and up.type == "expression_statement":
                    up = up.parent
                if up is not None and up.type in ("source_file", "declaration_list"):
                    return True
            if node.type == "attribute_item":
                inner = node.named_children[0] if node.named_children else None
                head = inner.named_children[0] if inner is not None and inner.named_children else None
                written = module.src[head.start_byte : head.end_byte].decode("utf-8", "replace") if head is not None else ""
                if written not in INERT_ATTRIBUTES:
                    return True
            todo.extend(node.named_children)
    return False


def _uncertain(model: Model, crate, here: list) -> str | None:
    """Why the module path of a file cannot be taken as certain, None when it can: a `#[path]`, a file the crate declares twice, a crate
    whose modules were not all read, an `include!`, a macro that may declare a module. A reading that cannot be sure runs the whole target."""
    from .rs_modules import attribute_facts

    tree = model.tree(crate)
    if tree.problems:
        return "ripple could not read every module of the crate"
    files = [m.file for m in here if not m.inline]
    if len(files) != len(set(files)):
        return "the crate declares a file more than once"
    seen: set[int] = set()
    for module in here:
        current = module
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if current.decl is not None and current.parent is not None:
                _, path_attr, cfg_path = attribute_facts(current.parent.src, current.decl)
                if path_attr is not None or cfg_path:
                    return "a module on the way is declared with #[path]"
            if re.search(rb"\binclude!", current.src):
                return f"{current.file} uses include!"
            for item in current.node.named_children:
                called = item.type == "macro_invocation" or (
                    item.type == "expression_statement" and item.named_children and item.named_children[0].type == "macro_invocation"
                )  # `m! { .. }` and `m!(..);` as an item
                if called and re.search(rb"\bmod\b", current.src[item.start_byte : item.end_byte]):
                    return f"a macro in {current.file} may declare a module"
            current = current.parent
    return None


def _prefixes(paths: list[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    """The module paths with those below another dropped (a prefix covers everything under it)."""
    kept: list[tuple[str, ...]] = []
    for path in sorted(set(paths), key=lambda p: (len(p), p)):
        if not any(path[: len(k)] == k for k in kept):
            kept.append(path)
    return tuple(sorted(kept))


def _crate_picks(model: Model, request: Request, given: str, groups: dict, rel: str, scope: str, below: set[tuple[str, str]]) -> int:
    """One pick per crate of `groups` ((root, kind) -> its modules in the file or directory). `below`: the test targets whose root file is
    in the scope, run whole. A crate root in the scope, or a mapping that is not certain, is the whole target, and the verdict says so."""
    count = 0
    for (root, kind), modules in sorted(groups.items()):
        crate = modules[0].crate
        if kind in ("bench", "build"):
            continue
        if (root, kind) in below:
            pick = Pick(given, root, kind)
        elif any(m.path == () for m in modules):
            pick = Pick(given, root, kind)
            request.notes.append(f"{given}: {root} is the root of the {kind} target, so every test of it ran")
        else:
            why = _uncertain(model, crate, modules)
            if why is not None:
                pick = Pick(given, root, kind)
                request.notes.append(f"{given}: the module path of {scope} is not certain ({why}), so every test of {root} ran")
            else:
                pick = Pick(given, root, kind, None, _prefixes([m.path for m in modules]))
        if pick not in request.picks:
            request.picks.append(pick)
            count += 1
    return count


def _file(model: Model, request: Request, given: str, rel: str) -> None:
    modules = _modules_of_file(model, rel, given)
    groups: dict[tuple[str, str], list] = {}
    for module in modules:
        groups.setdefault((module.crate.root, module.crate.kind), []).append(module)
    below = {(root, kind) for (root, kind) in groups if root == rel and kind == "test"}  # the root of an integration test is its whole target
    if not _crate_picks(model, request, given, groups, rel, rel, below):
        raise ValueError(f"{given!r}: {rel} is in a bench or build target, and check does not run those")
    for pick in request.picks:
        if pick.given == given and pick.prefixes:
            below_files = sorted(
                {
                    c.file
                    for m in modules
                    if m.crate.root == pick.root
                    for c in model.tree(m.crate).modules
                    if c.file != rel and _is_below(c, m)
                }
            )
            if below_files:
                request.notes.append(f"{given}: runs every test below its module, including those of submodules in other files ({', '.join(below_files[:3])})")


def _is_below(module, ancestor) -> bool:
    current = module.parent
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def _directory(model: Model, request: Request, given: str, rel: str) -> None:
    wholly = [p for p in model.workspace.packages.values() if _within(p.dir, rel)]
    for package in wholly:
        request.packages.append((given, package.dir))
    outer = model.package_of(rel)
    picked = 0
    if outer is not None and outer not in wholly:
        groups: dict[tuple[str, str], list] = {}
        below: set[tuple[str, str]] = set()  # the integration tests whose root file is below the directory: those targets, whole
        for module in model.modules_in(outer, rel):
            crate = module.crate
            groups.setdefault((crate.root, crate.kind), []).append(module)
            if crate.kind == "test" and module.path == () and _within(crate.root, rel):
                below.add((crate.root, crate.kind))
        picked = _crate_picks(model, request, given, groups, rel, rel, below)
    if not wholly and not picked:
        raise ValueError(f"{given!r}: no source of a library, binary, example or test target is below it")


# ---- the plan: the cargo runs that make exactly that -----------------------------------------------


@dataclass
class Invocation:
    """One `cargo test` run: where it runs, which packages and target it selects and the exact names it filters to.

    A run of picks that are not the whole target is `listing`: the test binary is first asked what it holds (`-- --list`), the picks are
    resolved against that list (`resolve`), and `names` becomes what the list says. `names` as planned is only what the ids said.
    """

    cwd: str  # repo-relative
    selectors: list[str]
    names: tuple[str, ...] = ()
    picks: list[Pick] = field(default_factory=list)  # what asked for it (empty: a whole workspace or package)
    target: Target | None = None
    listing: bool = False
    pending: bool = False  # the names wait for the list (a file or directory), so there is no command to make yet
    notes: list[str] = field(default_factory=list)

    def argv(self) -> list[str]:
        if self.pending and not self.names:
            raise RuntimeError("the names of this run are not known until the test binary has listed its tests")
        return BASE_ARGV + self.selectors + (["--", "--exact", *self.names] if self.names else [])

    def list_argv(self) -> list[str]:
        return BASE_ARGV + self.selectors + ["--", "--list"]

    def command(self) -> str:
        text = shlex.join(self.argv() if not self.pending or self.names else self.list_argv())
        return f"cd {shlex.quote(self.cwd)} && {text}" if self.cwd else text


@dataclass
class Resolution:
    """What a listing says of a run's picks: the exact names to run, which of them each pick stands for, and what the list could not give."""

    names: tuple[str, ...] = ()
    expected: dict[int, tuple[str, ...]] = field(default_factory=dict)  # index in the invocation's picks -> the listed names it covers
    total: int = 0  # how many tests the binary lists
    problems: list[str] = field(default_factory=list)


def _bare_path(name: str) -> str:
    return "::".join(_bare(part) for part in name.split("::"))


def resolve(inv: Invocation, listed: list[str]) -> Resolution:
    """The picks of a run against the names its test binary lists. The list is the binary's own, so a test a macro generates is in it.

    A name asked for must be listed, else there is no such test (a verdict problem, not a filter that quietly matches nothing); a
    module prefix is every listed test below it. `r#` is compared away (libtest keeps it: `tests::r#match`) and the listed spelling is used.
    """
    res = Resolution(total=len(listed))
    where = inv.target.name if inv.target else "the target"
    bare = {_bare_path(n): n for n in listed}
    chosen: dict[str, None] = {}
    for index, pick in enumerate(inv.picks):
        found: list[str] = []
        if pick.names:
            for name in pick.names:
                actual = bare.get(_bare_path(name))
                if actual is None:
                    res.problems.append(f"{pick.given}: no test named {name} exists in {where} (its test binary lists {_plural(len(listed), 'test')})")
                else:
                    found.append(actual)
        elif pick.prefixes:
            for prefix in pick.prefixes:
                head = "::".join(_bare(p) for p in prefix) + "::"
                found += [n for n in listed if _bare_path(n).startswith(head)]
            if not found:
                shown = ", ".join("::".join(p) for p in pick.prefixes)
                res.problems.append(f"{pick.given}: no test is listed below the module {shown} of {where} (its test binary lists {_plural(len(listed), 'test')})")
        res.expected[index] = tuple(dict.fromkeys(found))
        chosen.update(dict.fromkeys(found))
    res.names = tuple(sorted(chosen))
    return res


def _planned_names(picks: list[Pick]) -> tuple[tuple[str, ...], bool, bool]:
    """(names to filter by as the ids wrote them, whether the run waits for a list, whether it needs one at all) of a target's picks."""
    if any(p.whole for p in picks):
        return (), False, False
    ids = tuple(sorted({n for p in picks if p.names for n in p.names}))
    pending = any(p.prefixes for p in picks)
    return (() if pending else ids), pending, True


def _broken_reason(broken: dict[str, str], path: str) -> str:
    """Why cargo could not read the manifest above `path`, when it is one the discovery found it could not."""
    for directory, why in broken.items():
        if _within(path, directory):
            return f" (cargo cannot read {posixpath.join(directory, 'Cargo.toml')}: {why})"
    return ""


def _find_target(spaces: list[Space], pick: Pick, broken: dict[str, str]) -> tuple[Space, PackageInfo, Target]:
    for space in spaces:
        for package in space.packages:
            for target in package.targets:
                if target.root == pick.root and target.kind == pick.kind:
                    return space, package, target
    raise ValueError(f"{pick.given!r}: cargo lists no {pick.kind} target rooted at {pick.root}{_broken_reason(broken, pick.root)}")


def _selector(package: str, target: Target) -> list[str]:
    flag = {"lib": ["--lib"], "bin": ["--bin", target.name], "test": ["--test", target.name], "example": ["--example", target.name]}
    return ["-p", package] + flag[target.kind]


def plan(spaces: list[Space], request: Request, repo: Path, broken: dict[str, str] | None = None) -> list[Invocation]:
    """The `cargo test` runs that make exactly what was asked, and nothing broader.

    With nothing asked: `--workspace` in each workspace. A package asked for whole is `-p NAME` (all of its
    targets and its doc tests; every package of a workspace is `--workspace`). A test target is its own run,
    selected by package and by `--lib`, `--bin NAME` or `--test NAME`, with `-- --exact NAME ...`: a filter is
    shared by every binary of a run, so two targets (or two packages) that ask for different things are never
    in one run, and a name that two of them have runs only in the one asked for. Raises ValueError for a target
    `cargo test` would not run (a bench, `test = false`, a feature it needs that is off) or one it cannot filter.
    """
    if not spaces:
        raise ValueError("no Cargo.toml in the repository: ripple does not know where to run `cargo test` (pass --test-command)")
    if not request.picks and not request.packages:
        return [run for space in spaces if not space.nested for run in _whole(space, None)]

    broken = broken or {}
    runs: list[Invocation] = []
    by_space: dict[int, set[str]] = {}
    for given, pdir in request.packages:
        owner = next(((i, p) for i, s in enumerate(spaces) for p in s.packages if p.dir == pdir), None)
        if owner is None:
            raise ValueError(f"{given!r}: cargo lists no package in {pdir or '.'}{_broken_reason(broken, pdir)}")
        by_space.setdefault(owner[0], set()).add(owner[1].name)
    for index, names in sorted(by_space.items()):
        runs += _whole(spaces[index], names)

    grouped: dict[tuple[int, str, str, str], tuple[Target, list[Pick], list[str]]] = {}
    for pick in request.picks:
        space, package, target = _find_target(spaces, pick, broken)
        if package.name in by_space.get(spaces.index(space), set()):
            continue  # the package is asked for whole, and this is part of it
        notes = _refuse_unrunnable(pick, package, target, repo)
        if notes:  # a target with its own harness has no names to list: the whole target runs, and the verdict says so
            pick = Pick(pick.given, pick.root, pick.kind)
        key = (spaces.index(space), package.name, target.kind, target.name)
        _, picks, kept = grouped.get(key, (target, [], []))
        picks.append(pick)
        grouped[key] = (target, picks, kept + notes)
    for (index, package, _, _), (target, picks, notes) in sorted(grouped.items(), key=lambda kv: kv[0]):
        space = spaces[index]
        pdir = next(p.dir for p in space.packages if p.name == package)
        cwd = space.root if space.root is not None else pdir
        names, pending, listing = _planned_names(picks)
        runs.append(Invocation(cwd, _selector(package, target), names, picks, target, listing, pending, notes))
    return runs


def _refuse_unrunnable(pick: Pick, package: PackageInfo, target: Target, repo: Path) -> list[str]:
    """Refuse a target `cargo test` would not run. Returns notes when the pick must run wider than it was asked (a `harness = false` target)."""
    if target.kind not in ("lib", "bin", "test", "example"):
        raise ValueError(f"{pick.given!r}: that is in a {target.kind} target, and check does not run those")
    # an example is `test = false` unless its table says otherwise, and `cargo test --example NAME` runs its tests all the same
    if not target.test and target.kind != "example":
        raise ValueError(f"{pick.given!r}: the target {target.name} has `test = false`, so cargo builds no test binary for it")
    missing = [f for f in target.required if f not in package.enabled()]
    if missing:
        raise ValueError(
            f"{pick.given!r}: the target {target.name} requires the features {', '.join(missing)}, which are off "
            "(ripple passes none: pass --test-command to turn them on)"
        )
    if (pick.names or pick.prefixes) and harness_off(repo, package.manifest, target.kind, target.name):
        if pick.names:
            raise ValueError(f"{pick.given!r}: the target {target.name} has `harness = false`, so it has no test names to filter by; pass its file")
        return [f"{pick.given}: the target {target.name} has `harness = false` and no list of tests, so the whole target ran"]
    return []


def skipped_targets(spaces: list[Space], invocations: list[Invocation]) -> list[str]:
    """Notes for the test targets a whole-workspace or whole-package run skips without a word: those whose `required-features` are off.

    cargo leaves such a target out of `--workspace` silently (nothing is built and nothing printed), so it would otherwise
    be a narrower run than asked for that the output never mentions.
    """
    notes = []
    for inv in invocations:
        if inv.picks or inv.names:
            continue
        for space in spaces:
            for package in space.packages:
                named = inv.selectors == ["--workspace"] and inv.cwd == (space.root or "")
                named = named or (package.name in inv.selectors and inv.cwd in (space.root, package.dir))
                if not named:
                    continue
                for target in package.targets:
                    missing = [f for f in target.required if f not in package.enabled()]
                    if target.test and missing and target.kind in ("lib", "bin", "test", "example"):
                        notes.append(f"{package.name}/{target.name} ({target.root}) needs the features {', '.join(missing)}, which are off, so cargo skipped it")
    return sorted(set(notes))


def _whole(space: Space, names: set[str] | None) -> list[Invocation]:
    """Runs of a workspace's packages (all of them, or the named ones), whole."""
    every = {p.name for p in space.packages}
    if names is None or names == every:
        if space.root is not None:
            return [Invocation(space.root, ["--workspace"])]
        names = every
    if space.root is not None:
        return [Invocation(space.root, [a for n in sorted(names) for a in ("-p", n)])]
    # a workspace whose root is above the repo: run each package from its own directory, which cargo takes as that package alone
    return [Invocation(p.dir, ["-p", p.name]) for p in space.packages if p.name in names]


def harness_off(repo: Path, manifest: str, kind: str, name: str) -> bool:
    """Whether the manifest (repo-relative) turns libtest off for the target (`harness = false`). A manifest ripple cannot read turns nothing off."""
    try:
        from .rs_modules import _toml

        data = _toml().loads((repo / manifest).read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return False
    if kind == "lib":
        table = data.get("lib")
        return isinstance(table, dict) and table.get("harness") is False
    entries = data.get(kind)
    return isinstance(entries, list) and any(isinstance(e, dict) and e.get("name") == name and e.get("harness") is False for e in entries)


# ---- the output --------------------------------------------------------------------------------


@dataclass
class Artifact:
    """A test binary cargo says it built (a `compiler-artifact` with the test profile and an executable)."""

    package: str
    name: str
    kind: str
    root: str  # the crate root file as cargo wrote it (absolute)
    manifest: str  # the package's Cargo.toml as cargo wrote it (absolute)


@dataclass
class LibtestRun:
    """One libtest run: a `running N tests` line, the test lines under it and its closing `test result:` line."""

    declared: int | None = None  # N of `running N tests`; None: libtest printed none
    tests: dict[str, str] = field(default_factory=dict)  # name -> ok, FAILED or ignored
    result: dict | None = None  # the `test result:` line: verdict and counts; None when there was none


@dataclass
class Section:
    """What one test binary (or the doc tests of one crate) printed.

    A section is the output between two of cargo's `Running` / `Doc-tests` lines, and it can hold more than one libtest run: rustdoc on
    edition 2024 merges the doc tests it can and runs the ones it cannot (`compile_fail`, `standalone_crate`, an edition override) as a
    second run under the same `Doc-tests` line, and a test binary can print a run of its own. Each run is judged by itself; `declared`,
    `tests` and `result` are the section as the sum of its runs.
    """

    header: str  # "unittests src/lib.rs", "tests/it.rs", "doc-tests NAME"
    exe: str  # the executable's file name, "" for doc tests
    doc: bool = False
    runs: list[LibtestRun] = field(default_factory=list)
    blocks: dict[str, list[str]] = field(default_factory=dict)  # name -> the lines of its `---- name stdout ----` block
    died: str | None = None  # how cargo says the process ended, when it did not exit successfully
    other: list[str] = field(default_factory=list)  # what it printed that is not libtest's framing, the last lines
    listed: dict[str, str] = field(default_factory=dict)  # a `--list` run: name -> test or benchmark, as the binary itself lists them
    list_total: int | None = None  # the `N tests, M benchmarks` line of a `--list` run: N + M; None when there was none
    unparsed: list[str] = field(default_factory=list)  # lines that look like a test result and are none ripple knows

    @property
    def declared(self) -> int | None:
        """The tests the section's runs announced, added up; None when none of them announced any."""
        counts = [r.declared for r in self.runs if r.declared is not None]
        return sum(counts) if counts else None

    @property
    def tests(self) -> dict[str, str]:
        """The tests of every run of the section (a name that two runs print is one entry here: judge a run by `runs`)."""
        return {name: state for run in self.runs for name, state in run.tests.items()}

    @property
    def result(self) -> dict | None:
        """The result lines of the section's runs added up (FAILED when any run says so); None unless every run has one, and one has."""
        if not self.runs or any(r.result is None for r in self.runs):
            return None
        total = {k: sum(r.result[k] for r in self.runs) for k in self.runs[0].result if k != "verdict"}
        return {"verdict": "FAILED" if any(r.result["verdict"] != "ok" for r in self.runs) else "ok", **total}

    def open_run(self) -> LibtestRun:
        """The run the next line belongs to: the last one, or a first one for a line that comes before any `running N tests`."""
        if not self.runs:
            self.runs.append(LibtestRun())
        return self.runs[-1]


@dataclass
class Output:
    sections: list[Section] = field(default_factory=list)
    artifacts: dict[str, Artifact] = field(default_factory=dict)  # executable file name -> what it is
    build_finished: bool | None = None  # the success of the `build-finished` event; None when there was none
    compile_errors: list[str] = field(default_factory=list)
    cargo_errors: list[str] = field(default_factory=list)  # cargo's own `error:` lines, with the lines that explain them
    blocked: bool = False  # cargo waited for a lock another cargo held


def _span(message: dict) -> str:
    spans = [s for s in message.get("spans") or [] if isinstance(s, dict) and s.get("is_primary")]
    return f" ({spans[0].get('file_name')}:{spans[0].get('line_start')})" if spans else ""


def parse_output(text: str) -> Output:
    """The run read from its JSON events and libtest's sections. Reads the structure; no line is taken for a verdict by its words alone."""
    out = Output()
    current: Section | None = None
    in_failures = False
    block: str | None = None
    lines = [ANSI.sub("", raw).rstrip("\r") for raw in text.splitlines()]
    for index, line in enumerate(lines):
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if isinstance(event, dict) and isinstance(event.get("reason"), str):
                _event(out, event)
                continue
        running = RUNNING.match(line)
        doc = DOC_TESTS.match(line)
        if running or doc:
            in_failures, block = False, None
            if running:
                current = Section(running["what"], os.path.basename(running["exe"]))
            else:
                current = Section(f"doc-tests {doc['crate']}", "", doc=True)
            out.sections.append(current)
            continue
        if "Blocking waiting for file lock" in line:
            out.blocked = True
        if current is not None and not in_failures:
            if RUNNING_N.match(line):
                last = current.runs[-1] if current.runs else None
                if last is not None and (last.declared is not None or last.tests or last.result is not None):
                    current.runs.append(LibtestRun())  # a run after another in the same section (rustdoc's second run of doc tests)
                current.open_run().declared = int(RUNNING_N.match(line)["n"])
                continue
            tested = TEST_LINE.match(line)
            if tested and TEST_NAME.fullmatch(tested["name"]):
                current.open_run().tests[tested["name"]] = tested["status"]
                continue
            if TEST_LOOKING.match(line):
                current.unparsed.append(clip(line.strip()))
                continue
            listed = LIST_LINE.match(line)
            if listed and not line.startswith("test "):
                current.listed[listed["name"]] = listed["kind"]
                continue
            total = LIST_TOTAL.match(line)
            if total:
                current.list_total = int(total["tests"]) + int(total["benchmarks"])
                continue
        if current is not None and (match := RESULT.match(line)):
            if current.runs and current.runs[-1].result is not None:
                current.runs.append(LibtestRun())  # a second result line closes a run of its own, never overwrites the first
            current.open_run().result = {k: (v if k == "verdict" else int(v)) for k, v in match.groupdict().items()}
            in_failures, block = False, None
            continue
        if current is not None and line == "failures:" and not in_failures:
            in_failures = True
            continue
        if current is not None and in_failures:
            opened = FAILURE_BLOCK.match(line)
            if opened:
                block = opened["name"]
                current.blocks.setdefault(block, [])
            elif block is not None:
                current.blocks[block].append(line)
            continue
        if line.startswith("error"):
            out.cargo_errors.append(_error_with_cause(lines, index))
            continue
        died = DIED.search(line)
        if died and current is not None:
            current.died = died["how"]
            continue
        if current is not None and line.strip() and not line.startswith(("warning:", "Caused by:")):
            current.other = (current.other + [clip(line.strip())])[-6:]
    return out


def _error_with_cause(lines: list[str], index: int) -> str:
    """cargo's `error: ...` line and up to three lines under it that are not JSON events (the cause is usually there)."""
    found = [lines[index].strip()]
    for line in lines[index + 1 : index + 8]:
        if line.startswith("{"):
            break
        if line.strip() and (line.startswith((" ", "Caused by:", "Consider"))):
            found.append(line.strip())
        elif line.strip():
            break
        if len(found) == 4:
            break
    return " | ".join(found)


def _event(out: Output, event: dict) -> None:
    reason = event["reason"]
    if reason == "build-finished":
        out.build_finished = bool(event.get("success"))
    elif reason == "compiler-artifact":
        profile, target = event.get("profile") or {}, event.get("target") or {}
        executable = event.get("executable")
        if profile.get("test") and isinstance(executable, str):
            package = str(event.get("package_id", ""))
            out.artifacts[os.path.basename(executable)] = Artifact(
                package, str(target.get("name", "")), _kind(target.get("kind") or []), str(target.get("src_path", "")), str(event.get("manifest_path", ""))
            )
    elif reason == "compiler-message":
        message = event.get("message") or {}
        text = str(message.get("message", ""))
        if message.get("level") == "error" and not text.startswith("aborting due to"):
            line = f"{text}{_span(message)}"
            if line not in out.compile_errors:
                out.compile_errors.append(line)


# ---- the verdict -------------------------------------------------------------------------------


@dataclass
class Run:
    """One `cargo test` run and what came of it."""

    invocation: Invocation
    output: Output
    exit_code: int | None  # None: it was killed for taking too long
    timed_out: bool = False
    started: bool = True  # False: the deadline had passed before it began
    text: str = ""
    resolution: Resolution | None = None  # what the listing said of the picks, for a run made from a list
    pre: list[str] = field(default_factory=list)  # problems found before the run (a name the list does not have, a list that failed)
    skipped: bool = False  # nothing was run: the listing left nothing to run, and `pre` says why


def _package_name(package_id: str) -> str:
    """The package of a cargo package id (`path+file:///x/a#0.1.0` is `a`, `...#hy-phen@0.1.0` is `hy-phen`)."""
    head, _, tail = package_id.partition("#")
    if "@" in tail:
        return tail.split("@", 1)[0]
    return posixpath.basename(head.rstrip("/")) or package_id


def _failure_detail(section: Section, name: str) -> str | None:
    lines = section.blocks.get(name, [])
    for i, line in enumerate(lines):
        match = PANICKED.search(line.strip())
        if match:
            message = next((m.strip() for m in lines[i + 1 :] if m.strip()), "")
            return f"panicked at {match['where']}" + (f": {message}" if message else "")
    for line in lines:
        if line.startswith("error"):
            return line.strip()
    return next((m.strip() for m in lines if m.strip() and not m.startswith(("Test executable failed", "stderr:"))), None)


def _tail(text: str, count: int = 3) -> str:
    kept = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("{")]
    return " | ".join(kept[-count:])


def judge(
    repo: Path, runs: list[Run], notes: list[str], command: str, duration: float, timeout: float, model: Model | None = None
) -> dict:
    """The verdict of the runs, from their output. See the module docstring for what is never a pass."""
    problems: list[str] = []
    failures: list[str] = []
    counts = {"passed": 0, "failed": 0, "ignored": 0, "unreported": 0, "binaries": 0, "doc_test_crates": 0, "own_harness": 0, "empty_binaries": 0}
    several = len([r for r in runs if r.started and not r.skipped]) > 1
    notes = list(notes)
    model = model or Model(repo)

    for run in runs:
        out, inv = run.output, run.invocation
        label = f" (in `{inv.command()}`)" if several else ""
        explained = len(problems) + len(failures)
        if not run.started:
            problems.append(f"not run: the {timeout:g}s limit was used up before `{inv.command()}` began")
            continue
        problems.extend(f"{p}{label}" if several else p for p in run.pre)
        notes.extend(inv.notes)
        if run.skipped:
            continue
        if run.timed_out:
            why = " (cargo was waiting for a lock on the build directory that another cargo holds)" if out.blocked else ""
            problems.append(f"timed out after {timeout:g}s{label}: the run was killed before it finished{why}")
        built = out.build_finished is True
        if out.build_finished is False or (out.build_finished is None and not run.timed_out and not out.sections and run.exit_code not in (0, None)):
            reasons = out.compile_errors[:3] or [e for e in out.cargo_errors if "could not compile" not in e][:2] or out.cargo_errors[:2]
            problems.append(f"build failed{label}: {'; '.join(reasons)}" if reasons else f"build failed{label}")
            continue
        if not built and not out.sections and not run.timed_out:
            tail = _tail(run.text)
            problems.append(f"cargo test produced no test run{label}" + (f": {clip(tail)}" if tail else ""))
            continue

        if not run.timed_out:
            for exe, artifact in out.artifacts.items():
                if not any(s.exe == exe for s in out.sections):
                    problems.append(f"the test binary of {_package_name(artifact.package)}/{artifact.name} was built but never run{label}")
        before = dict(counts)
        for section in out.sections:
            _judge_section(repo, run, section, counts, problems, failures, notes, model, label)
        _check_picks(run, problems, notes)
        if len(problems) + len(failures) == explained:  # a run with a problem already has its reason
            _check_ran(run, counts, before, problems, notes, label, repo, model)
        if run.exit_code not in (0, None) and len(problems) + len(failures) == explained:
            tail = _tail(run.text)
            problems.append(f"cargo test exited {run.exit_code} but its output shows no failure{label}" + (f": {clip(tail)}" if tail else ""))

    started = [r for r in runs if r.started and not r.skipped]
    timed_out = any(r.timed_out for r in runs)
    codes = [r.exit_code for r in started]
    exit_code = None if timed_out else next((c for c in codes if c), 0)
    passed = not problems and not failures and counts["failed"] == 0 and all(c == 0 for c in codes) and bool(codes) and not any(r.skipped for r in runs)
    summary = f"{counts['passed']} passed, {counts['failed']} failed, {counts['ignored']} ignored"
    if counts["unreported"]:
        summary += f", {counts['unreported']} ended with no result"
    summary += f" in {_plural(counts['binaries'], 'test binary').replace('binarys', 'binaries')}"
    if counts["doc_test_crates"]:
        summary += f" and {_plural(counts['doc_test_crates'], 'doc-test run')}"
    if counts["own_harness"]:
        summary += f" ({_plural(counts['own_harness'], 'binary').replace('binarys', 'binaries')} with its own harness)"
    summary += "; timed out" if timed_out else ""
    if counts["ignored"] and passed:
        notes.append(f"{counts['ignored']} ignored tests were not run (cargo does not run #[ignore] tests unless told to)")
    if several:
        notes.append(f"{len(started)} `cargo test` runs, one per package and test target asked for (see command)")
    result = {
        "passed": passed,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_s": round(duration, 2),
        "command": clip(command, MAX_COMMAND),
        "summary": clip(summary),
        "failures": failures[:MAX_FAILURES],
        "failure_count": len(failures),
        "problems": [clip(p) for p in problems[:MAX_PROBLEMS]],
        "counts": counts,
    }
    if len(problems) > MAX_PROBLEMS:
        result["problems_omitted"] = len(problems) - MAX_PROBLEMS
    if notes:
        result["notes"] = [clip(n) for n in dict.fromkeys(notes)]
    return result


def _judge_section(repo, run, section, counts, problems, failures, notes, model, label) -> None:
    out = run.output
    artifact = out.artifacts.get(section.exe)
    where = f"{_package_name(artifact.package)}/{artifact.name}" if artifact else section.header
    if section.doc:
        counts["doc_test_crates"] += 1
    else:
        counts["binaries"] += 1
    for libtest in section.runs:  # a name two runs both print is counted in each
        counts["passed"] += sum(1 for s in libtest.tests.values() if s == "ok")
        counts["failed"] += sum(1 for s in libtest.tests.values() if s == "FAILED")
        counts["ignored"] += sum(1 for s in libtest.tests.values() if s == "ignored")

    for line in section.unparsed[:3]:
        problems.append(f"{where}: libtest printed a test line ripple cannot read (it may be a test it did not count): {line}{label}")
    for libtest in section.runs:
        for name, state in libtest.tests.items():
            if state == "FAILED":
                failures.append(clip(_failure_line(repo, section, artifact, name, model)))

    own_harness = not section.doc and not section.runs
    if own_harness and artifact is not None and _harness_off(repo, artifact):
        if section.died is not None:
            problems.append(f"{where}: its own test harness did not exit successfully ({section.died}){label}")
        elif run.timed_out:
            problems.append(f"{where}: still running when it was killed{label}")
        else:
            counts["own_harness"] += 1
            flag = {"lib": "--lib", "bin": f"--bin {artifact.name}", "test": f"--test {artifact.name}", "example": f"--example {artifact.name}"}.get(artifact.kind, "")
            if flag and any(f"to rerun pass `{flag}`" in e for e in out.cargo_errors):
                problems.append(f"{where}: its own test harness failed ({next((ln for ln in reversed(section.other) if not ln.startswith('note:')), 'no output')}){label}")
            elif run.exit_code == 0:
                notes.append(f"{where} has its own test harness (harness = false): no test was counted, it is judged on its exit status alone")
        return
    many = len(section.runs) > 1

    def named(index: int) -> str:  # a section of several libtest runs says which run it means
        return f"{where} (run {index + 1} of {len(section.runs)})" if many else where

    unfinished = [i for i, libtest in enumerate(section.runs) if libtest.result is None]
    if not section.runs or unfinished:
        if run.timed_out:
            problems.append(f"{where}: still running when it was killed{label}")
        elif section.died is not None:
            last = section.runs[-1] if section.runs else LibtestRun()
            reported = f" after {_plural(len(last.tests), 'test')} reported of {last.declared}" if last.declared is not None else ""
            problems.append(f"{named(len(section.runs) - 1)}: the test binary died ({section.died}){reported}{label}")
        elif not section.runs:
            problems.append(f"{where}: the test binary printed no libtest output and no result{label}" + (f": {section.other[-1]}" if section.other else ""))
        else:
            for index in unfinished:  # every run needs its own result line: a section is no more finished than its last run
                libtest = section.runs[index]
                counts["unreported"] += max((libtest.declared or 0) - len(libtest.tests), 0)
                problems.append(
                    f"{named(index)}: the test binary exited without reporting a result (running {libtest.declared} tests, "
                    f"{len(libtest.tests)} reported){label}"
                )
        return
    for index, libtest in enumerate(section.runs):
        result, states = libtest.result, libtest.tests
        ok = sum(1 for s in states.values() if s == "ok")
        bad = sum(1 for s in states.values() if s == "FAILED")
        ignored = sum(1 for s in states.values() if s == "ignored")
        for key, seen in (("passed", ok), ("failed", bad), ("ignored", ignored)):
            if result[key] != seen:
                problems.append(f"{named(index)}: its result line says {result[key]} {key} but {seen} tests are listed as such{label}")
        total = result["passed"] + result["failed"] + result["ignored"] + result["measured"]
        if libtest.declared is not None and libtest.declared != total:
            problems.append(f"{named(index)}: it announced {libtest.declared} tests but its result line counts {total}{label}")
        if result["verdict"] == "FAILED" and bad == 0:
            problems.append(f"{named(index)}: failed with no failing test listed{label}")
        if result["verdict"] == "ok" and bad:
            problems.append(f"{named(index)}: its result line says ok with {bad} failing tests{label}")


def _harness_off(repo: Path, artifact: Artifact) -> bool:
    """Whether the manifest of the artifact's package turns libtest off for its target."""
    manifest = _relative(repo, artifact.manifest) if artifact.manifest else None
    return manifest is not None and harness_off(repo, manifest, artifact.kind, artifact.name)


def _failure_line(repo: Path, section: Section, artifact: Artifact | None, name: str, model: Model | None) -> str:
    detail = _failure_detail(section, name)
    suffix = f": {detail}" if detail else ""
    if section.doc:
        return f"FAILED doctest {name}{suffix}"
    ident = None
    if artifact is not None:
        root = _relative(repo, artifact.root) if artifact.root else None
        if root is not None:
            try:
                found = (model or Model(repo)).locate(root, name)
            except ValueError:
                found = None
            ident = f"{found[0]}::{found[1]}" if found else f"{root}::{name}"
    return f"FAILED {ident or f'{section.header}::{name}'}{suffix}"


def _is_module_of_another_target(repo: Path, artifact: Artifact, model: Model) -> bool:
    """Whether the root file of a test target is also a module of another target of its package (`tests/util.rs` that `tests/it.rs`
    pulls in with `mod util;`): a helper, not a test file with no test. Only ever used to turn a problem into a note, so a failure to
    read the sources leaves the problem."""
    root = _relative(repo, artifact.root) if artifact.root else None
    if root is None:
        return False
    try:
        crates = model.workspace.crates
        mine = next((c for c in crates if c.root == root and c.kind == "test"), None)
        if mine is None:
            return False
        return any(
            m.file == root
            for c in crates
            if c.package is mine.package and c is not mine
            for m in model.tree(c).modules
        )
    except (ValueError, OSError):
        return False


def _check_ran(run: Run, counts: dict, before: dict, problems: list[str], notes: list[str], label: str, repo: Path, model: Model) -> None:
    """Each run, and each integration-test target in it, must have run something: evidence of tests, or an own harness.

    A run that executes nothing and exits 0 is not a pass whatever the other runs of the verdict did; and a test target
    (a file under tests/) that holds no test is not a test that passed, unless another target of the package reads its file as a
    module (a shared helper): that is a note. A unit-test binary with no tests is ordinary (most bins have none) and is counted,
    not flagged.
    """
    out = run.output
    if run.timed_out or out.build_finished is False or run.invocation.names:
        return  # named tests are checked one by one, and a kill or a build failure says why nothing ran
    empty_targets = 0
    for section in out.sections:
        if section.doc or section.result is None or section.tests:
            continue
        artifact = out.artifacts.get(section.exe)
        if artifact is not None and artifact.kind == "test":
            where = f"{_package_name(artifact.package)}/{artifact.name}"
            if _is_module_of_another_target(repo, artifact, model):
                counts["empty_binaries"] += 1
                notes.append(f"{where} ({_relative(repo, artifact.root)}) holds no test: another target of the package reads it as a module, so it is a helper")
                continue
            empty_targets += 1
            problems.append(f"{where}: the test target ran no test{label}")
        else:
            counts["empty_binaries"] += 1
    ran = counts["passed"] - before["passed"] + counts["failed"] - before["failed"]
    if ran == 0 and counts["own_harness"] == before["own_harness"] and not empty_targets:
        if counts["ignored"] > before["ignored"]:
            problems.append(f"every test that ran was ignored, so nothing was checked{label}")
        else:
            problems.append(f"no test ran{label}")


def _check_picks(run: Run, problems: list[str], notes: list[str]) -> None:
    """What was asked for must have a result of its own, and nothing else may have run in its place.

    A run made from a listing (`run.resolution`) is judged against the list: each name the list gave for a pick needs a result of
    its own, nothing outside the selection may have run, and the result line's `filtered out` has to be what the list leaves over,
    which a binary that merely imitates libtest's output does not know.
    """
    inv, out = run.invocation, run.output
    # names the run was made with are asked for whether or not a request gave them (a hand-made command)
    picks = inv.picks or [Pick(name, "", "", (name,)) for name in inv.names]
    if run.timed_out or out.build_finished is False or not picks:
        return  # the kill or a build failure already says why it has no result
    exe = [s for s in out.sections if not s.doc]
    if len(exe) != 1:
        problems.append(f"{inv.target.name if inv.target else 'the target'}: expected one test binary to run, {len(exe)} did")
        return
    section = exe[0]
    res = run.resolution
    if res is not None:
        _check_listed(run, section, res, problems, notes)
        return
    asked = {name for pick in picks if pick.names for name in pick.names}
    for pick in picks:
        if pick.names is None:
            if not any(s in ("ok", "FAILED") for s in section.tests.values()) and not (section.declared is None and section.result is None and not section.tests):
                problems.append(f"{pick.given}: no test ran there")
            continue
        for name in pick.names:
            state = section.tests.get(name)
            if state in ("ok", "FAILED"):
                continue  # a failure is listed as such
            if state == "ignored":
                problems.append(f"{pick.given}: {name} is marked #[ignore], so it did not run")
            else:
                problems.append(f"{pick.given}: no test named {name} ran (the filter matched nothing)")
    if asked and not any(p.names is None for p in picks):
        extra = sorted(n for n in section.tests if n not in asked)
        if extra:
            problems.append(f"{_plural(len(extra), 'test')} ran that {'was' if len(extra) == 1 else 'were'} not asked for: {', '.join(extra[:5])}")


def _check_listed(run: Run, section: Section, res: Resolution, problems: list[str], notes: list[str]) -> None:
    inv = run.invocation
    for index, pick in enumerate(inv.picks):
        expected = res.expected.get(index, ())
        states = {name: section.tests.get(name) for name in expected}
        if pick.names:
            for name, state in states.items():
                if state in ("ok", "FAILED"):
                    continue  # a failure is listed as such
                if state == "ignored":
                    problems.append(f"{pick.given}: {name} is marked #[ignore], so it did not run")
                else:
                    problems.append(f"{pick.given}: {name} is listed but has no result of its own")
        elif pick.prefixes and expected:
            missing = [n for n, state in states.items() if state is None]
            if missing:
                problems.append(f"{pick.given}: {_plural(len(missing), 'listed test')} below it have no result of their own: {', '.join(missing[:5])}")
            ignored = [n for n, state in states.items() if state == "ignored"]
            if ignored and len(ignored) == len(expected):
                problems.append(f"{pick.given}: every test below it is marked #[ignore], so none ran")
            elif ignored:
                notes.append(f"{pick.given}: {_plural(len(ignored), 'test')} below it are marked #[ignore] and were not run")
    extra = sorted(n for n in section.tests if n not in set(res.names))
    if extra:
        problems.append(f"{_plural(len(extra), 'test')} ran that {'was' if len(extra) == 1 else 'were'} not asked for: {', '.join(extra[:5])}")
    if section.result is not None and section.result["filtered"] != res.total - len(res.names):
        problems.append(
            f"its result line says {section.result['filtered']} tests were filtered out, but the binary lists {res.total} and "
            f"{len(res.names)} were selected (the output is not what libtest would print for this run)"
        )


# ---- what the environment can change ------------------------------------------------------------

# Measured against the real toolchain: CARGO_TERM_COLOR, CARGO_TERM_PROGRESS_WHEN, RUST_TEST_THREADS, an `[alias]` named test (cargo
# ignores it, with a warning) and CARGO_TERM_QUIET / CARGO_TERM_VERBOSE (overridden above) leave the output as it is read. These
# change what is built or run, or how libtest prints, so a verdict made under them says so.
ENV_NOTES = {
    "RUSTFLAGS": "it can change what is built (a warning it makes an error is reported as a failed build)",
    "CARGO_ENCODED_RUSTFLAGS": "it can change what is built (a warning it makes an error is reported as a failed build)",
    "CARGO_BUILD_RUSTFLAGS": "it can change what is built (a warning it makes an error is reported as a failed build)",
    "CARGO_BUILD_TARGET": "tests are built for that target, and run only if this machine can run it",
    "RUST_TEST_NOCAPTURE": "libtest prints a test's output between its own lines, which can hide a result line from ripple (a problem, never a pass)",
    "RUSTC_WRAPPER": "a compiler wrapper is in use",
    "RUSTC_WORKSPACE_WRAPPER": "a compiler wrapper is in use",
}
CONFIG_NOTES = {
    ("build", "rustflags"): "it can change what is built",
    ("build", "target"): "tests are built for that target, and run only if this machine can run it",
    ("build", "rustc-wrapper"): "a compiler wrapper is in use",
    ("build", "rustc-workspace-wrapper"): "a compiler wrapper is in use",
    ("env", None): "it sets environment variables for the run",
}


def environment_notes(repo: Path, cwds: list[str]) -> list[str]:
    """What in the environment or the repo can change what `cargo test` builds or runs, named so the verdict is read with it."""
    notes = [f"{key} is set: {why}" for key, why in ENV_NOTES.items() if key in os.environ]
    notes += [f"{key} is set: it runs the tests through another program" for key in sorted(os.environ) if key.startswith("CARGO_TARGET_") and key.endswith("_RUNNER")]
    seen: set[str] = set()
    for cwd in cwds or [""]:
        directory = cwd
        while True:
            for name in ("rust-toolchain.toml", "rust-toolchain"):
                rel = posixpath.join(directory, name)
                if rel not in seen and (repo / rel).is_file():
                    seen.add(rel)
                    notes.append(f"{rel} selects the toolchain `cargo` runs, which may not be the one on PATH")
            for name in (".cargo/config.toml", ".cargo/config"):
                rel = posixpath.join(directory, name)
                if rel not in seen and (repo / rel).is_file():
                    seen.add(rel)
                    notes += _config_notes(repo, rel)
            if not directory:
                break
            directory = posixpath.dirname(directory)
    return notes


def _config_notes(repo: Path, rel: str) -> list[str]:
    try:
        from .rs_modules import _toml

        data = _toml().loads((repo / rel).read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return [f"{rel} could not be read, so what it changes is not known"]
    found = []
    for (table, key), why in CONFIG_NOTES.items():
        value = data.get(table)
        if isinstance(value, dict) and (key is None and value or key in value):
            found.append(f"{rel} sets {table}.{key or '*'}: {why}")
    targets = data.get("target")
    if isinstance(targets, dict) and any(isinstance(t, dict) and "runner" in t for t in targets.values()):
        found.append(f"{rel} sets a target runner: the tests run through another program")
    return found


# ---- running it --------------------------------------------------------------------------------


def discover(repo: Path, model: Model, cargo: str, started: float, timeout: float) -> tuple[list[Space], dict[str, str]]:
    """Every cargo workspace of the repo, from `cargo metadata --no-deps` (read-only, nothing is built), and the manifests cargo could not read.

    A workspace inside another's directory that is not a member of it (its `exclude`: a fixture, a vendored crate) is marked
    nested: it is run when a path asks for it and left out of a run with no arguments. A manifest that cargo cannot read
    and that sits inside a workspace's directory is returned in the second part (directory -> what cargo said); one that
    sits in no workspace fails the run now, since there is then no knowing what the repo's tests are.
    """
    spaces: list[Space] = []
    covered: set[str] = set()
    broken: dict[str, str] = {}
    for manifest in manifests_of(repo, model.files):
        directory = posixpath.dirname(manifest)
        if directory in covered:
            continue
        left = timeout - (time.monotonic() - started)
        if left <= 0:
            raise ValueError(f"the {timeout:g}s limit was used up before cargo had said what the workspaces are")
        outcome = run_process([cargo] + METADATA_ARGV[1:], repo / directory, left, merge_stderr=False)
        inside = any(s.root is not None and _within(directory, s.root) for s in spaces)
        if outcome.timed_out or outcome.returncode != 0:
            first = next((ln for ln in outcome.stderr.splitlines() if ln.strip()), "no output").strip()
            if not inside:
                raise ValueError(f"`cargo metadata` failed for {manifest}: {clip(first)}")
            broken[directory] = clip(first)
            continue
        space = parse_metadata(outcome.stdout, repo)
        covered.update(p.dir for p in space.packages)
        if space.root is not None:
            covered.add(space.root)
        space.nested = any(s.root is not None and space.root is not None and s.root != space.root and _within(space.root, s.root) for s in spaces)
        spaces.append(space)
    return spaces, broken


def _make_run(invocation: Invocation, cargo: str, repo: Path, started: float, timeout: float) -> Run:
    """Run one invocation. One that is not the whole target is first asked what its test binary holds (`-- --list`, which builds the
    binary once: the run after it finds it built), and runs exactly the names the list gives for its picks."""
    left = timeout - (time.monotonic() - started)
    if left <= 0:
        return Run(invocation, Output(), None, started=False)
    cwd = repo / invocation.cwd if invocation.cwd else repo
    if not invocation.listing:
        outcome = run_process([cargo] + invocation.argv()[1:], cwd, left)
        return Run(invocation, parse_output(outcome.stdout), outcome.returncode, outcome.timed_out, text=outcome.stdout)
    listed = run_process([cargo] + invocation.list_argv()[1:], cwd, left)
    out = parse_output(listed.stdout)
    if listed.timed_out or out.build_finished is False or (out.build_finished is None and not out.sections):
        # the listing is what failed to build or to finish: it is judged as the run, and says so
        return Run(invocation, out, listed.returncode, listed.timed_out, text=listed.stdout)
    binaries = [sec for sec in out.sections if not sec.doc]
    problem = None
    if listed.returncode != 0:
        problem = f"`cargo test -- --list` exited {listed.returncode}: {clip(_tail(listed.stdout))}"
    elif len(binaries) != 1:
        problem = f"`cargo test -- --list` ran {len(binaries)} test binaries where one was expected"
    elif binaries[0].list_total is None or binaries[0].list_total != len(binaries[0].listed):
        problem = "the test binary printed no list of its tests that adds up (it may have exited before libtest started)"
    if problem is not None:
        return Run(invocation, Output(), None, skipped=True, pre=[problem])
    resolution = resolve(invocation, list(binaries[0].listed))
    invocation.names = resolution.names
    if not resolution.names:
        return Run(invocation, Output(), None, skipped=True, pre=resolution.problems, resolution=resolution)
    left = timeout - (time.monotonic() - started)
    if left <= 0:
        return Run(invocation, Output(), None, started=False)
    outcome = run_process([cargo] + invocation.argv()[1:], cwd, left)
    return Run(
        invocation, parse_output(outcome.stdout), outcome.returncode, outcome.timed_out,
        text=outcome.stdout, resolution=resolution, pre=resolution.problems,
    )  # fmt: skip


def run(repo: Path, tests: list[str] | None, timeout: float) -> dict:
    """Run the Rust tests asked for (or every workspace's), and judge the output. Raises ValueError for a request it cannot make."""
    repo = repo.resolve()
    check_tests(repo, tests or [])
    model = Model(repo)
    request = parse_wants(repo, tests or [], model) if tests else Request()
    cargo = shutil.which("cargo")
    if cargo is None:
        raise ValueError("`cargo` is not on PATH: ripple runs Rust tests with `cargo test` (or pass --test-command)")
    started = time.monotonic()
    spaces, broken = discover(repo, model, cargo, started, timeout)
    invocations = plan(spaces, request, repo, broken)
    notes = skipped_targets(spaces, invocations)
    skipped = [sp.root or "." for sp in spaces if sp.nested]
    if skipped and not tests:
        notes.append(f"{_plural(len(skipped), 'cargo workspace')} inside another workspace but not part of it were not run (name one to run it): {', '.join(skipped[:5])}")
    if broken:
        notes.append(f"{_plural(len(broken), 'Cargo.toml')} cargo cannot read, inside a workspace, were not run: {', '.join(sorted(broken)[:5])}")
    runs = [_make_run(invocation, cargo, repo, started, timeout) for invocation in invocations]
    command = "; ".join(r.invocation.command() for r in runs)
    notes += request.notes + environment_notes(repo, sorted({i.cwd for i in invocations}))
    return judge(repo, runs, notes, command, time.monotonic() - started, timeout, model)
