"""`check` for Go: run `go test -json` and read the verdict from its event stream.

The verdict is never the exit code alone and never a match on the text of the log. `go test -json`
prints one JSON event per line (the Action of each says what happened), and what comes out here is
what those events prove. These cases come out as what they are, never as "passed", and each has a
captured stream under tests/fixtures/go_check/captures:

- a build or vet failure (an event with Action build-fail, and the package's own fail),
- a package with no test files (a package-level skip: fine inside `./...`, not as the only result),
- a `-run` pattern that matches nothing (go exits 0 and the package passes with no test event),
- a panic, and go's own timeout (a test with a run event and no result),
- a test binary that exits early (`syscall.Exit(0)` in a test: exit code 0, and the package never
  gets a result event at all) or is killed.

A requested test must have a result event of its own: the same name in another package, or the parent
of a subtest that matched none, does not stand in for it.

This is a runner for checker.py: `run(repo, tests, timeout)` returns the same keys as the generic
check (passed, exit_code, timed_out, duration_s, command, summary, failures, failure_count) plus
`problems` (why a run is not a clean pass), `counts` and `notes`. checker.RUNNERS names it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .checker import MAX_COMMAND, MAX_FAILURES, check_tests, clip, run_process
from .go_modules import GoMod, is_ignored_dir, read_gomod
from .go_tests import selected

TEST_FUNCTION = re.compile(r"^func ((?:Test|Example|Fuzz)\w*)\(", re.MULTILINE)
PREFIXES = ("Test", "Example", "Fuzz")
# What `go test` runs. Flags on the command line win over GOFLAGS (in the environment or in the go env file, in
# any spelling: -run, -test.run, quoted), so the ones that decide which tests run, or whether any does, are
# set here and not read back from it:
#   -count=1   a run made now, not a replay of a cached one (and not -count=0)
#   -skip=     no test is left out by a -skip pattern
#   -list=     go test does not just list the tests
#   -c=false   go test does not just compile the test binary
#   -fuzz=     no fuzzing run in place of the tests
# and -run= when the run has no pattern of its own (see Invocation.argv). What stays the user's own build
# configuration (-tags, -short, -vet, -exec, -race, -timeout, -failfast, ...) is named in a note when
# `go env GOFLAGS` is set.
BASE_ARGV = ["go", "test", "-json", "-count=1", "-skip=", "-list=", "-c=false", "-fuzz="]
SILENT_OK = (
    "reported ok but never printed PASS, so its test binary may have exited before running its tests "
    "(a TestMain or init that calls os.Exit(0))"
)
SILENT_PRINTED = (
    "reported ok with no test event and without go's \"no tests to run\" notice, so its test binary may have exited "
    "before running its tests (a TestMain or init that prints PASS and calls os.Exit(0))"
)
MAX_PROBLEMS = 10
TAIL = 6


# ---- the request: what the caller asked to run -------------------------------------------------


@dataclass(frozen=True)
class Want:
    """One thing asked for: the tests of a package directory, or a test (and perhaps a subtest) in it."""

    dir: str  # repo-relative, "" for the repo root
    levels: tuple[str, ...]  # ("TestA",) or ("TestA", "sub"), () for every test below dir
    given: str  # the argument as it was given, for messages

    @property
    def name(self) -> str:
        return "/".join(self.levels)

    @property
    def label(self) -> str:
        """What a message calls it: the argument, and for a whole file the test in it as well (the file alone says nothing)."""
        return f"{self.given}::{self.name}" if self.levels and "::" not in self.given else self.given


@dataclass
class Invocation:
    """One `go test` run: the module it runs in, its -run pattern and the packages it names."""

    module: GoMod
    run: str | None
    packages: list[str]

    def argv(self) -> list[str]:
        return BASE_ARGV + (["-run", self.run] if self.run else ["-run="]) + self.packages

    def command(self) -> str:
        text = shlex.join(self.argv())
        return f"cd {shlex.quote(self.module.dir)} && {text}" if self.module.dir else text


def quote_meta(text: str) -> str:
    """text as a Go regexp that matches it literally (regexp.QuoteMeta)."""
    return re.sub(r"([\\.+*?()|\[\]{}^$])", r"\\\1", text)


def is_test_name(name: str) -> bool:
    """Whether Go's test runner would take `name` as a test, example or fuzz target (Benchmark: no)."""
    for prefix in PREFIXES:
        if name.startswith(prefix):
            return selected(name, prefix)  # go_tests.selected: Go's rule (unicode.IsLower), the one the collection uses
    return False


def contained(repo: Path, relative: str) -> Path | None:
    """repo / relative with its links followed, or None unless that is inside the repo (or `relative` cannot be a path).

    Every path built from a string a caller supplied goes through this before anything lists, reads or
    probes it: a symlink out of the repo, an absolute path and `..` all end here as None.
    """
    try:
        path = (repo / relative).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    return path if path.is_relative_to(repo) else None


def find_modules(repo: Path) -> list[GoMod]:
    """Every module in the repo, as the go tool sees them: a go.mod, skipping vendor, testdata and `.x` or `_x` directories."""
    repo = repo.resolve()
    found = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if not is_ignored_dir(d) and d != "node_modules")
        if "go.mod" in files:
            rel = Path(root).relative_to(repo).as_posix()
            rel = "" if rel == "." else rel
            file = contained(repo, f"{rel}/go.mod" if rel else "go.mod")  # a go.mod that links out of the repo is not read
            try:
                text = file.read_text(encoding="utf-8", errors="replace") if file else ""
            except OSError:
                text = ""
            found.append(read_gomod(rel, text) or GoMod(rel, ""))
    return sorted(found, key=lambda m: m.dir)


def _within(directory: str, parent: str) -> bool:
    """Whether the repo-relative `directory` is `parent` or below it."""
    return parent == "" or directory == parent or directory.startswith(parent + "/")


def _relative(directory: str, module: GoMod) -> str:
    """`directory` as the package pattern `go test` takes from the module's own directory: ./a/b, or . for the root."""
    rest = directory[len(module.dir):].lstrip("/") if module.dir else directory
    return "./" + rest if rest else "."


OUTPUT_COMMENT = re.compile(r"^\s*//\s*(?:unordered output|output):", re.IGNORECASE | re.MULTILINE)


def _functions(path: Path) -> tuple[list[str], list[str]]:
    """(the test, example and fuzz functions a _test.go file declares that `go test` runs, the examples it only compiles).

    An Example with no `// Output:` or `// Unordered output:` comment is compiled and never run, so a
    verdict that waited for it would wait for ever.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    runs, compiled = [], []
    for match in TEST_FUNCTION.finditer(text):
        name = match[1]
        if not is_test_name(name):
            continue
        if name.startswith("Example"):
            end = text.find("\n}", match.end())
            body = text[match.end(): end if end >= 0 else len(text)]
            if not OUTPUT_COMMENT.search(body):
                compiled.append(name)
                continue
        runs.append(name)
    return runs, compiled


def declared_tests(path: Path) -> list[str]:
    """The names of the test, example and fuzz functions a _test.go file declares that `go test` runs."""
    return _functions(path)[0]


def _exists(path: Path) -> bool:
    """path.exists(), with a file system error on a caller-supplied path turned into a refusal."""
    try:
        return path.exists()
    except OSError as error:
        raise ValueError(f"cannot be used as a path in the repository: {error.strerror or 'file system error'}") from error


NAME_MAX, PATH_MAX = 255, 4096


def _unusable_as_path(text: str) -> bool:
    """Whether `text` cannot be a path on this file system: a name over NAME_MAX bytes, a path over PATH_MAX, a NUL, or text
    that does not encode (the file system would raise OSError, or ValueError with the whole string in it)."""
    try:
        raw = os.fsencode(text.partition("::")[0] if "::" in text else text)
    except (UnicodeError, ValueError):
        return True
    return b"\x00" in raw or len(raw) >= PATH_MAX or any(len(part) > NAME_MAX for part in raw.split(b"/"))


def _plain_import_path(path: str) -> bool:
    """Whether `path` is a slash-separated import path with no empty, `.` or `..` element and nothing a path could hide in."""
    return all(part not in ("", ".", "..") for part in path.split("/")) and not any(
        c in "\\" or ord(c) < 0x20 or ord(c) == 0x7F for c in path
    )


SYMBOL_ID = re.compile(r"^(?P<package>[^:\s]+):(?P<name>[A-Za-z_]\w*)(?P<subtests>/.*)?$")


def normalize(repo: Path, tests: list[str]) -> list[str]:
    """The tests with each Go symbol id as `impact` hands it out turned into the `file::TestName` form.

    `impact` lists a Go test function among the callers to update (a Go repo has no collected tests, so
    its `tests` list is empty), as `example.com/m/pkg:TestName` (`.../pkg_test:TestName` for an external
    test package) under the file it is in. That id names no path, so it is looked up in the repo's own
    modules; one that finds no declaring file is left as it was, for the checks that follow to refuse.
    """
    repo = repo.resolve()
    modules = [m for m in find_modules(repo) if m.path]
    result = []
    for given in tests:
        if isinstance(given, str) and _unusable_as_path(given):
            # the file system would answer with an OSError (ENAMETOOLONG) from deep inside check_tests: refuse it here
            raise ValueError(f"not a test path or id: {clip(given, 60)!r} is too long or malformed to be a path in the repository")
        match = SYMBOL_ID.match(given) if isinstance(given, str) and "::" not in given else None
        # An id is only looked up when its package is a plain import path, and when it is not a path of the repo's
        # that exists. A given that leaves the repo is left as it was: check_tests refuses it, and nothing was read.
        if (
            match is None
            or not _plain_import_path(match["package"])
            or (inside := contained(repo, given)) is None
            or _exists(inside)
        ):
            result.append(given)
            continue
        name = match["name"]
        found = None
        for package in dict.fromkeys([match["package"], match["package"].removesuffix("_test")]):
            for module in sorted((m for m in modules if package == m.path or package.startswith(m.path + "/")), key=lambda m: -len(m.path)):
                directory = package_dir(module, package)
                file = directory is not None and locate(repo, directory, name)
                if file:
                    found = f"{file}::{name}{match['subtests'] or ''}"
                    break
            if found:
                break
        result.append(found or given)
    return result


def parse_wants(repo: Path, tests: list[str]) -> list[Want]:
    """The tests asked for, as the directories and test names they name. Raises ValueError for one `go test` cannot be asked for.

    The forms: a directory (every package below it), a _test.go file (the tests it declares), and
    `file_test.go::TestName` or `file_test.go::TestName/sub/case` (a test, or a subtest of it). The
    caller has already run checker.check_tests: no option, no `@file`, nothing outside the repo.
    """
    repo = repo.resolve()
    wants = []
    for given in tests:
        if not isinstance(given, str) or any(ord(c) < 0x20 or ord(c) == 0x7F for c in given):
            raise ValueError(f"not a Go test path or id: {given!r}")
        path_part, sep, name_part = given.partition("::")
        path = contained(repo, path_part)
        if path is None:
            raise ValueError(f"{given!r} is not a file or directory inside the repository")
        directory = path.relative_to(repo).as_posix()
        directory = "" if directory == "." else directory
        if path.is_dir():
            if sep:
                raise ValueError(f"{given!r}: a directory has no test names; name a _test.go file")
            wants.append(Want(directory, (), given))
            continue
        if not path.name.endswith("_test.go"):
            raise ValueError(f"{given!r} is not a Go test file (_test.go) or a directory")
        declared, compiled_only = _functions(path)
        parent = PurePosixPath(directory).parent.as_posix()
        parent = "" if parent == "." else parent
        if not sep:
            if not declared:
                raise ValueError(f"{given!r} declares no Test, Fuzz or Example (with an output comment) function for `go test` to run")
            wants.extend(Want(parent, (name,), given) for name in declared)
            continue
        # go rewrites a space in a subtest's name to an underscore, and -run and the events use that name
        levels = tuple(level.replace(" ", "_") for level in name_part.split("/"))
        if not all(levels):
            raise ValueError(f"{given!r}: an empty test or subtest name")
        if levels[0] in compiled_only:
            raise ValueError(
                f"{given!r}: {levels[0]} has no // Output: or // Unordered output: comment, so `go test` "
                "compiles it and never runs it"
            )
        if levels[0] not in declared:
            shown = ", ".join(declared[:5]) + (f" and {len(declared) - 5} more" if len(declared) > 5 else "")
            raise ValueError(
                f"{given!r}: {path_part} declares no test {levels[0]!r} "
                f"(it declares: {shown or 'none'}); pass file_test.go::TestName[/subtest]"
            )
        wants.append(Want(parent, levels, given))
    return wants


def _has_packages(repo: Path, directory: str) -> bool:
    """Whether a Go file the go tool reads sits in `directory` or below it, outside every nested module."""
    start = contained(repo, directory)
    if start is None:
        return False
    for root, dirs, files in os.walk(start):
        dirs[:] = [
            d
            for d in dirs
            if not is_ignored_dir(d) and d != "node_modules" and not (Path(root) / d).is_symlink()  # a link is not walked
            and not (Path(root) / d / "go.mod").is_file()
        ]
        if any(f.endswith(".go") and not f.startswith(("_", ".")) for f in files):
            return True
    return False


def plan(modules: list[GoMod], wants: list[Want], repo: Path | None = None) -> list[Invocation]:
    """The `go test` runs that make exactly what was asked, and nothing broader.

    With nothing asked: `./...` in every module (a nested module is not part of its parent's `./...`).
    A directory is `./dir/...` in the module that holds it, when that module has a package below the
    directory outside every nested module (`go test` fails on a pattern that matches no package, so a
    directory of nested modules is only their runs; `repo` is where that is looked up), and `./...` in
    every module below it. A test name is `-run` in the package directory it was found in: a pattern is
    shared only by packages that ask for the same thing, so a test that two packages both have runs only in
    the one asked for, and subtests never share a pattern unless the result is exactly what was asked.
    """
    if not modules:
        raise ValueError("no go.mod in the repository: ripple does not know where to run `go test` (pass --test-command)")
    if not wants:
        return [Invocation(m, None, ["./..."]) for m in modules]

    def holder(directory: str) -> GoMod | None:
        holders = [m for m in modules if _within(directory, m.dir)]
        return max(holders, key=lambda m: len(m.dir)) if holders else None

    def owner(directory: str) -> GoMod:
        module = holder(directory)
        if module is None:
            raise ValueError(f"{directory or '.'!r} is not inside any Go module (no go.mod above it)")
        return module

    whole: dict[str, set[str]] = {}
    for want in (w for w in wants if not w.levels):
        below = [m for m in modules if _within(m.dir, want.dir)]
        for module in below:
            whole.setdefault(module.dir, set()).add("./...")
        if not any(m.dir == want.dir for m in modules):
            module = holder(want.dir)
            if module is None and not below:
                owner(want.dir)  # raises: no module holds it, and none is below it
            elif module is not None and (repo is None or not below or _has_packages(repo.resolve(), want.dir)):
                whole.setdefault(module.dir, set()).add(_relative(want.dir, module) + "/...")
    by_dir = {m.dir: m for m in modules}
    runs = [Invocation(by_dir[d], None, sorted(patterns)) for d, patterns in sorted(whole.items())]

    covered = [w.dir for w in wants if not w.levels]
    by_package: dict[str, list[Want]] = {}
    for want in (w for w in wants if w.levels and not any(_within(w.dir, c) for c in covered)):
        by_package.setdefault(want.dir, []).append(want)
    shared: dict[tuple[str, str], set[str]] = {}
    for directory, group in sorted(by_package.items()):
        module = owner(directory)
        for pattern in _patterns(group):
            shared.setdefault((module.dir, pattern), set()).add(_relative(directory, module))
    for (module_dir, pattern), packages in sorted(shared.items()):
        runs.append(Invocation(by_dir[module_dir], pattern, sorted(packages)))
    return runs


def _patterns(group: list[Want]) -> list[str]:
    """The -run patterns for the wants of one package: one for the tests asked for whole, one per test and depth of subtests."""
    patterns = []
    plain = sorted({w.levels[0] for w in group if len(w.levels) == 1})
    if plain:
        patterns.append(f"^({'|'.join(quote_meta(n) for n in plain)})$")
    # A test asked for whole already runs its subtests, and so does a subtest asked for whole: drop what a shorter ask covers.
    deeper = {w.levels for w in group if len(w.levels) > 1 and w.levels[0] not in plain}
    keep = [p for p in deeper if not any(q != p and p[: len(q)] == q for q in deeper)]
    # One pattern per parent, alternating only the last level. Alternating every level would also run the cells
    # nobody asked for: x/1 and y/2 as (x|y)/(1|2) runs x/2 and y/1 too.
    leaves: dict[tuple[str, ...], set[str]] = {}
    for levels in keep:
        leaves.setdefault(levels[:-1], set()).add(quote_meta(levels[-1]))
    for parent, last in sorted(leaves.items()):
        head = "/".join(f"^{quote_meta(level)}$" for level in parent)
        patterns.append(f"{head}/^({'|'.join(sorted(last))})$")
    return patterns


# ---- the event stream --------------------------------------------------------------------------


@dataclass
class Package:
    path: str
    tests: dict[str, str] = field(default_factory=dict)  # test name -> run, pass, fail or skip, in the order it started
    detail: dict[str, str] = field(default_factory=dict)  # test name -> the first line it printed that is not framing
    result: str | None = None  # the package's own pass, fail or skip: absent when the test binary never reported
    failed_build: str | None = None
    tail: list[str] = field(default_factory=list)  # the last lines the package printed outside any test
    last_started: str | None = None
    # Whether the test binary printed PASS outside any test. It does when it ran its tests to the end (a package
    # with nothing to run prints it too, after "no tests to run"); a binary that exited 0 before m.Run, from a
    # TestMain or an init, prints only the ok line, and its package still reports pass.
    printed_pass: bool = False
    # Whether go said there was nothing to run: "testing: warning: no tests to run" from the binary, or the
    # "[no tests to run]" suffix on its ok line. Benchmarks only, helpers only and output-less examples only print
    # both; a TestMain that prints PASS itself and exits 0 prints neither.
    nothing_to_run: bool = False


@dataclass
class Stream:
    packages: dict[str, Package] = field(default_factory=dict)
    build_output: dict[str, list[str]] = field(default_factory=dict)
    build_failed: list[str] = field(default_factory=list)  # import paths with a build-fail event
    garbage: list[str] = field(default_factory=list)  # lines that are not events


def parse_stream(text: str) -> Stream:
    """The events of a `go test -json` run, folded by package and test. Read from the Action fields, not the log text."""
    stream = Stream()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if not isinstance(event, dict) or not isinstance(event.get("Action"), str):
            stream.garbage.append(clip(line.strip()))
            continue
        action = event["Action"]
        if action in ("build-output", "build-fail"):
            key = str(event.get("ImportPath", ""))
            if action == "build-fail":
                stream.build_failed.append(key)
            else:
                stream.build_output.setdefault(key, []).append(str(event.get("Output", "")).rstrip("\n"))
            continue
        path = event.get("Package")
        if not isinstance(path, str):
            continue
        package = stream.packages.setdefault(path, Package(path))
        name = event.get("Test")
        if name is not None and not isinstance(name, str):
            name = None
        if action == "output":
            _output(package, name, str(event.get("Output", "")), event.get("OutputType") == "frame")
        elif name is None:
            if action in ("pass", "fail", "skip"):
                package.result = action
                if isinstance(event.get("FailedBuild"), str):
                    package.failed_build = event["FailedBuild"]
        elif action == "run":
            # a benchmark gets a run event and never a result one (its numbers are output), so it is not "unfinished"
            package.tests.setdefault(name, "bench" if name.startswith("Benchmark") else "run")
            package.last_started = name
        elif action in ("pass", "fail", "skip"):
            if package.tests.get(name) == "bench" and action != "fail":
                continue  # a benchmark that passes or skips is still not a test: only one that fails is a failure
            package.tests[name] = action
    return stream


def _output(package: Package, name: str | None, text: str, frame: bool) -> None:
    line = text.rstrip("\n")
    shown = line.strip()
    if name is None and shown == "PASS":
        package.printed_pass = True
    if name is None and (shown == "testing: warning: no tests to run" or shown.endswith("[no tests to run]")):
        package.nothing_to_run = True
    if frame or not shown or shown.startswith(("=== ", "--- ")):
        return
    if name is None:
        package.tail = (package.tail + [clip(shown)])[-TAIL:]
    elif name not in package.detail:
        package.detail[name] = clip(shown)


# ---- the verdict -------------------------------------------------------------------------------


@dataclass
class Run:
    """One `go test` run and what came of it."""

    invocation: Invocation
    stream: Stream
    exit_code: int | None  # None: it was killed for taking too long
    timed_out: bool = False
    stderr: str = ""
    started: bool = True  # False: the deadline had passed before it began


def package_dir(module: GoMod, import_path: str) -> str | None:
    """The repo-relative directory of one of the module's packages, from its import path (None: not this module's)."""
    if not module.path:
        return None
    if import_path == module.path:
        return module.dir
    if import_path.startswith(module.path + "/"):
        rest = import_path[len(module.path) + 1:]
        return f"{module.dir}/{rest}" if module.dir else rest
    return None


def locate(repo: Path, directory: str, top: str) -> str | None:
    """The _test.go file in the directory that declares the test function `top`, repo-relative."""
    repo = repo.resolve()
    base = contained(repo, directory)
    if base is None:
        return None
    try:
        files = sorted(p for p in base.iterdir() if p.name.endswith("_test.go"))
    except OSError:
        return None
    for path in files:
        real = contained(repo, path.relative_to(repo).as_posix())
        try:
            if real is not None and real.is_file() and top in declared_tests(real):
                return path.relative_to(repo).as_posix()
        except OSError:
            continue
    return None


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _tail(text: str) -> str:
    return " | ".join(line.strip() for line in text.strip().splitlines()[-3:] if line.strip())


# What a test is when two runs disagree: the worse of the two. A test that ended with no result (the binary exited
# under it) is worse than one that passed, and a skip is the least: it did not run, so it never hides a failure.
STATE_RANK = {"skip": 1, "pass": 2, "unreported": 3, "run": 4, "fail": 5}
NO_PACKAGES = re.compile(r'go: warning: ".*" matched no packages|no packages to test')


def _empty_holder_run(run: "Run", runs: list["Run"]) -> bool:
    """Whether `run` is a module run of `./dir/...` that found no package because every Go file under dir belongs to a
    nested module or is one go leaves out of the build, while a nested module below dir ran packages of its own.

    Go says exactly `matched no packages` for that and exits 1, with no event at all. It is an empty run, not a
    failure, only when something below the directory did run: with nothing below it, the message stays a problem.
    """
    stream = run.stream
    if not run.started or run.timed_out or run.invocation.run is not None or stream.packages or stream.build_failed or stream.garbage:
        return False
    lines = [line.strip() for line in run.stderr.splitlines() if line.strip()]
    if not lines or not all(NO_PACKAGES.fullmatch(line) for line in lines):
        return False
    module = run.invocation.module.dir
    for pattern in run.invocation.packages:
        if not (pattern.startswith("./") and pattern.endswith("/...") and pattern != "./..."):
            return False
        directory = f"{module}/{pattern[2:-4]}" if module else pattern[2:-4]
        if not any(other.stream.packages and _within(other.invocation.module.dir, directory) for other in runs if other is not run):
            return False
    return True


def judge(
    repo: Path, runs: list[Run], wants: list[Want], command: str, duration: float, timeout: float, goflags: str | None = None
) -> dict:
    """The verdict of the runs, from their events. See the module docstring for what is never a pass.

    A package, a test and a subtest are counted once however many runs report them (asking for two subtests of one
    test can take two runs), and a test two runs disagree about is the worse of the two. goflags is what `go env
    GOFLAGS` says: it only goes in a note, since the flags that could narrow a run are set on the command line.
    """
    problems: list[str] = []
    notes: list[str] = []
    failures: list[str] = []
    counts = {
        "passed": 0, "failed": 0, "skipped": 0, "unreported": 0, "subtests": 0, "packages": 0,
        "packages_without_test_files": 0,
    }  # fmt: skip
    unnamed: list[str] = []  # packages that passed with no test event but go's notice that there was nothing to run
    several = len(runs) > 1
    packages_seen: set[str] = set()
    without_files: set[str] = set()
    not_built: set[str] = set()
    tests_seen: dict[tuple[str, str], str] = {}  # (import path, test name) -> state, the worst any run saw
    where_seen: dict[tuple[str, str], str | None] = {}
    detail_seen: dict[tuple[str, str], str] = {}
    empty_holders = {id(run) for run in runs if _empty_holder_run(run, runs)}

    for run in runs:
        module, stream = run.invocation.module, run.stream
        label = f" (in module {module.path or module.dir or '.'})" if several else ""
        before = len(problems)
        failing = False
        if not run.started:
            problems.append(f"not run: the {timeout:g}s limit was used up before `{run.invocation.command()}` began")
            continue
        if id(run) in empty_holders:
            continue
        if run.timed_out:
            problems.append(f"timed out after {timeout:g}s{label}: the run was killed before it finished")
        for line in stream.garbage[:3]:
            problems.append(f"go test -json printed a line that is not an event{label}: {line}")
        for key in stream.build_failed:
            lines = [ln for ln in stream.build_output.get(key, []) if ln.strip() and not ln.startswith("#")]
            problems.append(f"build failed: {key}: {'; '.join(lines[:3])}" if lines else f"build failed: {key}")
        for package in stream.packages.values():
            packages_seen.add(package.path)
            where = package_dir(module, package.path)
            states = package.tests
            for name, state in states.items():
                if state == "bench":
                    continue  # a benchmark has no result event of its own: go reports it in the output
                if state == "pass" and "/" not in name and package.result is None and name == package.last_started:
                    state = "unreported"  # test2json closes the test a binary was in when it exited by itself, with a pass nothing gave
                if state in ("fail", "run"):
                    failing = True
                key = (package.path, name)
                if STATE_RANK[state] > STATE_RANK.get(tests_seen.get(key, ""), 0):
                    tests_seen[key] = state
                where_seen.setdefault(key, where)
                if package.detail.get(name):
                    detail_seen.setdefault(key, package.detail[name])
            unfinished = [n for n, s in states.items() if s == "run" and "/" not in n]
            if package.failed_build:
                not_built.add(package.path)
                if package.failed_build not in stream.build_failed:  # a go that does not send build-fail events
                    problems.append(f"build failed: {package.path}")
            elif package.result == "skip" and not states:
                without_files.add(package.path)
            elif package.result is None:
                if run.timed_out:
                    if unfinished:
                        problems.append(f"{package.path}: still running when it was killed: {', '.join(unfinished[:5])}")
                else:
                    last = f" (the last test started was {package.last_started})" if package.last_started else ""
                    problems.append(f"{package.path}: the test binary exited without reporting a result{last}{label}")
            elif package.result == "fail" and not any(s in ("fail", "run") for s in states.values()):
                tail = "; ".join(package.tail[-3:])
                problems.append(f"{package.path}: failed with no failing test{': ' + tail if tail else ''}")
            elif package.result == "pass":
                if unfinished:
                    problems.append(f"{package.path}: reported pass with tests that never finished: {', '.join(unfinished[:5])}")
                if not package.printed_pass:
                    # ok with no evidence that the tests ran to the end, or that there was nothing to run
                    problems.append(f"{package.path}: {SILENT_OK}{label}")
                elif not states:
                    if package.nothing_to_run:
                        if package.path not in unnamed:
                            unnamed.append(package.path)
                    else:
                        # a line that says PASS is not go's: only its "no tests to run" notice shows a package had none
                        problems.append(f"{package.path}: {SILENT_PRINTED}{label}")
        if run.exit_code not in (0, None) and len(problems) == before and not failing:
            tail = _tail(run.stderr)
            problems.append(f"go test exited {run.exit_code} but its events show no failure{label}" + (f": {clip(tail)}" if tail else ""))
        if not stream.packages and not stream.build_failed and not stream.garbage and not run.timed_out:
            tail = _tail(run.stderr)
            problems.append(f"go test produced no result{label}" + (f": {clip(tail)}" if tail else ""))

    for key, state in tests_seen.items():
        path, name = key
        if "/" in name:
            counts["subtests"] += 1
        elif state == "unreported":
            counts["unreported"] += 1
        else:
            counts[{"pass": "passed", "skip": "skipped"}.get(state, "failed")] += 1
        if state in ("fail", "run"):
            where = where_seen[key]
            file = locate(repo, where, name.split("/")[0]) if where is not None else None
            ident = f"{file or path}::{name}" + (" (did not finish)" if state == "run" else "")
            why = detail_seen.get(key)
            failures.append(clip(f"FAILED {ident}" + (f": {why}" if why else "")))
    counts["packages"] = len(packages_seen)
    counts["packages_without_test_files"] = len(without_files)
    built = len(not_built)

    every = [(run, package) for run in runs for package in run.stream.packages.values()]
    for want in wants:
        _check_want(want, every, runs, problems)
    problems = list(dict.fromkeys(problems))  # a problem two runs (or two wants) share is said once
    if goflags and goflags.strip():
        notes.append(
            f"GOFLAGS is set ({clip(goflags.strip(), 80)}) and go applies it to every run. ripple puts -count=1, -skip=, "
            "-list=, -c=false, -fuzz= and -run on the command line, which win over it, so none of those can narrow the "
            "run; what is left (-tags, -short, -vet, -exec, -race, -timeout and the rest) is your own build configuration"
        )
    ran = counts["passed"] + counts["failed"]
    if ran == 0 and not wants and not problems:
        if counts["skipped"]:
            problems.append("every test that ran was skipped, so nothing was checked")
        else:
            detail = []
            if counts["packages_without_test_files"]:
                detail.append(f"{_plural(counts['packages_without_test_files'], 'package')} with no test files")
            if unnamed:
                detail.append(f"{_plural(len(unnamed), 'package')} that ran no test")
            problems.append("no tests ran" + (f" ({', '.join(detail)})" if detail else ""))
    if unnamed and ran:
        notes.append(f"{_plural(len(unnamed), 'package')} passed without running a test: {', '.join(unnamed[:3])}")
    if counts["packages_without_test_files"] and ran:
        notes.append(f"{_plural(counts['packages_without_test_files'], 'package')} have no test files")
    if several:
        notes.append(f"{len(runs)} `go test` runs, one per module and per distinct -run pattern (see command)")

    timed_out = any(r.timed_out for r in runs)
    judged = [r for r in runs if r.started and id(r) not in empty_holders]
    codes = [r.exit_code for r in judged]
    exit_code = None if timed_out else next((c for c in codes if c), 0)
    passed = not problems and not failures and counts["failed"] == 0 and all(c == 0 for c in codes) and bool(codes)
    summary = f"{counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped"
    if counts["unreported"]:
        summary += f", {counts['unreported']} ended with no result"
    if counts["subtests"]:
        summary += f" (and {_plural(counts['subtests'], 'subtest')})"
    summary += f" in {_plural(counts['packages'], 'package')}"
    summary += f"; {built} failed to build" if built else ""
    summary += "; timed out" if timed_out else ""
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
        result["notes"] = [clip(n, 400) for n in notes]
    return result


def _check_want(want: Want, every: list[tuple[Run, Package]], runs: list[Run], problems: list[str]) -> None:
    """What was asked for must have a result of its own: the same name in another package does not stand in."""
    if any(not r.started or r.timed_out for r in runs) or any(r.stream.build_failed for r in runs):
        return  # the limit, the kill or a build failure already says why it has no result
    here = [(r, p) for r, p in every if (d := package_dir(r.invocation.module, p.path)) is not None and _within(d, want.dir)]
    if not want.levels:
        if not any(s in ("pass", "fail", "run") for _, p in here for s in p.tests.values()):
            problems.append(f"{want.label}: no test ran there")
        return
    exact = [p for r, p in here if package_dir(r.invocation.module, p.path) == want.dir]
    states = [p.tests[want.name] for p in exact if want.name in p.tests]
    if any(s in ("pass", "fail", "run") for s in states):
        return  # a failure or an unfinished test is listed as such
    if "skip" in states:
        problems.append(f"{want.label}: the test was skipped, so it did not run")
    elif not exact:
        problems.append(f"{want.label}: no package ran there")
    else:
        problems.append(f"{want.label}: no test of that name ran (the pattern matched nothing)")


# ---- running it --------------------------------------------------------------------------------


def run(repo: Path, tests: list[str] | None, timeout: float) -> dict:
    """Run the Go tests asked for (or every module's), and judge the events. Raises ValueError for a request it cannot make."""
    repo = repo.resolve()
    check_tests(repo, tests or [])
    wants = parse_wants(repo, tests or [])
    invocations = plan(find_modules(repo), wants, repo)
    go = shutil.which("go")
    if go is None:
        raise ValueError("`go` is not on PATH: ripple runs Go tests with `go test -json` (or pass --test-command)")
    started = time.monotonic()
    runs = []
    for invocation in invocations:
        left = timeout - (time.monotonic() - started)
        if left <= 0:
            runs.append(Run(invocation, Stream(), None, started=False))
            continue
        cwd = repo / invocation.module.dir if invocation.module.dir else repo
        outcome = run_process([go] + invocation.argv()[1:], cwd, left, merge_stderr=False)
        runs.append(
            Run(invocation, parse_stream(outcome.stdout), outcome.returncode, outcome.timed_out, outcome.stderr)
        )
    command = "; ".join(r.invocation.command() for r in runs)
    goflags = _go_env_flags(go, repo, timeout - (time.monotonic() - started))
    return judge(repo, runs, wants, command, time.monotonic() - started, timeout, goflags)


GO_ENV_LIMIT = 10  # seconds `go env GOFLAGS` may take: it only feeds a note, so it never holds a verdict up for long


def _go_env_flags(go: str, repo: Path, left: float) -> str:
    """What `go env GOFLAGS` says here: the environment's GOFLAGS, or the go env file's (`go env -w GOFLAGS=...`), which
    the process environment never shows. It only feeds a note, so it is asked for within what is left of the timeout
    (and at most GO_ENV_LIMIT seconds), and an answer that does not come, or a go that fails, is no note."""
    if left <= 0:
        return ""
    try:
        outcome = run_process([go, "env", "GOFLAGS"], repo, min(left, GO_ENV_LIMIT), merge_stderr=False)
    except OSError:
        return ""
    return outcome.stdout.strip() if outcome.returncode == 0 and not outcome.timed_out else ""
