"""`check` for Java on Gradle: run the `test` task and read the verdict from what Gradle itself reports about each test.

This is the Gradle half of java_check.py (which decides, in `build_tool`, that a repo with Gradle files at the root and
no pom.xml comes here). The contract is the same: `run(repo, tests, timeout)` returns the keys of the generic check
(passed, exit_code, timed_out, duration_s, command, summary, failures, failure_count) plus `problems` (why a run is not a
clean pass), `counts` and `notes`.

THE SHAPE OF THE PROOF. Ripple does not decide which tests exist. Gradle (and the JUnit Platform under it) decides that,
by rules ripple must not copy, and a copy always drifts: parameterized, nested, inherited, dynamic and templated tests,
display names, meta-annotations, conditions. So the question "did every test run" is put to Gradle: a small init script of
ripple's registers a test listener on every `Test` task and writes, per test, the class, the method name, the outcome and
the time, and per suite (the whole run, each test JVM, each class, each parameterized method) the outcome and Gradle's own
totals, one JSON line each, to a file named inside the script. The verdict is read from that record. Nothing in the pass
path reads a test source. (Source is read only to turn an id the caller wrote, `com.acme.CartTest.addsItems()`, into the
class and `--tests` pattern Gradle is asked for.)

A PASS is the one claim made, and needs all of these, each from the record or from Gradle's exit:
- Gradle exited 0, in time, and the record names the root build's task graph (the init script ran);
- the record is whole: every line complete (a cut-off last line is a truncated record), under the size cap;
- every `test` task of the build that the run was to execute was EXECUTED by Gradle in this run (not UP-TO-DATE,
  FROM-CACHE, SKIPPED or never reached, and not left out of the task graph), or says it has no sources (NO-SOURCE, a note);
- every executed `Test` task has its closing record, and the tests recorded agree with the totals Gradle gave for the
  task (a record that lost lines does not add up);
- no suite ended SKIPPED without an assumption: Gradle ends a class, and the test JVM's own suite, SKIPPED when the JVM
  stopped inside it. A test that calls `System.exit(0)` makes Gradle report BUILD SUCCESSFUL and skip the rest of the JVM's
  work; that, a `Runtime.halt`, an exit in a static initializer or `@AfterAll`, in a repeated or dynamic test, and a JVM
  killed all end the suite this way (21 shapes measured on Gradle 9.9.1, see tests/test_check_gradle_live.py). The JVM's own
  suite (the one named `Gradle Test Executor N`) and a class suite are the ones read: a skipped method container (a disabled
  `@ParameterizedTest`) ends SKIPPED too, and is an ordinary skip. A TEST that was skipped with a time above 0 and no assumption
  is NOT read as an exit on its own: a `@Disabled` test takes 1 ms often enough, and an assumption in `@BeforeAll` skips every
  test of the class with times above 0 and no assumption on the tests, both in suites Gradle ended normally;
- at least one test ran (not skipped), none failed, and what was asked for ran: a class by a test of it, a method by a
  test whose name Gradle gives as that method (`addsItems()`, or the parameterized `discounts(int)[1]`).

Anything else is not a pass: with a failing test it is a failure, and when ripple cannot prove it ran it is `problems`
(unverified), never "passed". Cases that come out as not-a-pass: a task that did not run, a build that failed before its
tests (compilation, dependency resolution), a filter that matched nothing, a test JVM that crashed, a time limit,
`ignoreFailures` (Gradle exits 0 with a failure in the record: the failure is listed), every test skipped, a record that
disagrees with Gradle's exit status.

NOT A REFUSAL, a note: tests the build's own `test` task deselects (`excludeTags`, `exclude`, a filter) were not run, and
the note says what the task narrows; a `test` task with no sources (NO-SOURCE); other `Test` tasks (`integrationTest`)
that `test` does not run, as `mvn test` does not run failsafe.

BUILDS THAT ARE NOT THE REPO'S. The init script does nothing in a build whose `gradle.parent` is set: `buildSrc` and the
builds of `includeBuild` (a convention-plugin build, `pluginManagement { includeBuild }`) are configured with the same
init script, and their tests (or lack of them) are not the repo's tests. Only the root build is read.

WHAT IS TRUSTED. The build under test is arbitrary code ripple runs: it can append lines to the record itself, and a
build that means to forge a pass can. Nothing here defends against that: the first record of a task and the first graph
win, which only guards against a build that is merely wrong. A pass proves what the build did honestly, not that the build
is honest. Likewise a Gradle user home with `init.d` scripts, `GRADLE_OPTS` and similar are named in `notes`, not read.

The command is `<gradlew or gradle> --no-daemon --console=plain --no-build-cache --no-configuration-cache
--no-configure-on-demand --continue -I <init script> test` (every project's `test`) or, for what was asked for,
`:proj:test --tests <pattern> ...`. The flags keep the answer honest: no daemon left behind, no restoring of an earlier
run's outputs from a cache, a configuration the build reads in full, and `--continue` so an independent project still runs
after one fails. The init script also makes every `Test` task out of date and uncacheable, so a build that already ran is run again.

Ids and paths are the ones java_check.py accepts for Java, so `impact`'s ids do not depend on the build tool. A class is
`--tests com.acme.shop.CartTest` (Gradle runs its `@Nested` classes with it), a method `--tests com.acme.shop.CartTest.addsItems`
and a method of a nested class `--tests 'com.acme.shop.CartTest$Receipts.receiptHasTotal'` (the binary name).

Which launcher: the repo's own `gradlew` is preferred to `gradle` on PATH (the Gradle version the repo pins; the same
policy as `mvnw` in java_check.py). It runs repo-provided code (a shell script and a jar) and may download a Gradle
distribution on first use, which is the repo's own build. A `gradlew` that is a link to a file outside the repo, or is not a
file, is refused and never run.

SUPPORTED GRADLE: 8.10 and newer. Measured with real runs on 8.10.2 and 9.9.1 (JDK 21): the exit shapes, the assumption shapes
(`assumeTrue(false)` in a test, in `@BeforeAll`, in a nested class, a disabled class, a disabled parameterized method) and the
suites of a real project give the same record on both, except that Gradle 8 has no `TestResult.assumptionFailure`, so a skip is
never known to be an assumption there (and on 8.10.2 no ordinary shape needed it: their suites end SUCCESS). `gradle --version` is
asked first, and an older Gradle is refused with the version found and the oldest supported, before anything runs. Not measured and
so not claimed: Gradle older than 8.10, and Kotlin, Groovy and Spock tests and TestNG (the same listener sees them). JUnit 4 was run
on both (tests/test_check_gradle_live_junit4.py).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import java_check as jc
from .checker import MAX_COMMAND, MAX_FAILURES, check_tests, clip, run_process

MAX_PROBLEMS = 10
MIN_GRADLE = (8, 10)  # the oldest Gradle whose runs were measured (see the docstring)
MAX_NAMES = 5
MAX_RECORD_BYTES = 96_000_000
MAX_RECORD_LINES = 400_000
# a project path ripple will put in a task name: `:`, `:core`, `:libs:util`
PROJECT_PATH = re.compile(r"^(?::[\w.\-]+)*:?$")

# The init script. No `$` in it (Groovy would read a GString). Every fact is one JSON line written in one write under a lock,
# so a build that dies leaves whole lines and at most one cut one. It does nothing for a build with a parent (buildSrc,
# an included build). The model is read when the task graph is ready, so `--tests` is already in the filters.
INIT_SCRIPT = r"""
import groovy.json.JsonOutput

if (gradle.parent == null) {
    def sink = '@@MANIFEST@@'
    def lock = new Object()
    def out = new FileOutputStream(sink, true)
    def emit = { Map line ->
        byte[] bytes = (JsonOutput.toJson(line) + '\n').getBytes('UTF-8')
        synchronized (lock) { out.write(bytes); out.flush() }
    }
    def safe = { Closure c -> try { c() } catch (Throwable e) { null } }
    def why = { Throwable e ->
        def parts = []
        while (e != null && parts.size() < 4) { parts << (e.message ?: e.class.simpleName); e = e.cause }
        parts.join(' <- ').take(400)
    }
    def depth = { TestDescriptor d ->
        int n = 0
        def up = d.parent
        while (up != null) { n++; up = up.parent }
        n
    }
    emit([event: 'init', gradle: gradle.gradleVersion])
    // a failure of this script is a fact about the script, never about the tests: it is written down, not thrown
    def guard = { Closure c ->
        try { c() } catch (Throwable e) {
            try { emit([event: 'listener_error', message: why(e)]) } catch (Throwable ignored) { }
        }
    }
    // `TestResult.assumptionFailure` is not there before Gradle 9: absent, nothing is read as skipped by an assumption
    def assumed = { TestResult r -> safe { r.assumptionFailure != null } == true }

    gradle.allprojects { Project p ->
        p.tasks.withType(Test).configureEach { Test t ->
            t.outputs.upToDateWhen { false }
            t.outputs.doNotCacheIf('ripple needs the tests to run') { true }
            t.addTestListener(new TestListener() {
                void beforeSuite(TestDescriptor d) { }
                void beforeTest(TestDescriptor d) { }
                void afterSuite(TestDescriptor d, TestResult r) {
                    guard {
                        emit([event: 'suite', task: t.path, name: d.name, cls: d.className, level: depth(d),
                              result: r.resultType.name(), tests: r.testCount, ok: r.successfulTestCount,
                              failed: r.failedTestCount, skipped: r.skippedTestCount, ms: r.endTime - r.startTime,
                              assumed: assumed(r),
                              error: r.exceptions ? why(r.exceptions[0]) : null])
                    }
                }
                void afterTest(TestDescriptor d, TestResult r) {
                    guard {
                        emit([event: 'test', task: t.path, cls: d.className, name: d.name,
                              result: r.resultType.name(), ms: r.endTime - r.startTime,
                              assumed: assumed(r),
                              error: r.exceptions ? why(r.exceptions[0]) : null])
                    }
                }
            })
        }
    }

    gradle.taskGraph.whenReady { graph -> guard {
        def projects = []
        gradle.rootProject.allprojects.each { Project p ->
            def sets = []
            def ss = p.extensions.findByName('sourceSets')
            if (ss != null) {
                ss.each { s -> sets << [name: s.name, java: safe { s.java.srcDirs*.path }, classes: safe { s.output.classesDirs.files*.path }] }
            }
            def tests = p.tasks.withType(Test).collect { Test t ->
                [name: t.name, path: t.path, enabled: t.enabled, ignoreFailures: t.ignoreFailures,
                 scheduled: graph.hasTask(t),
                 include: safe { t.includes as List }, exclude: safe { t.excludes as List },
                 filterIncludes: safe { t.filter.includePatterns as List },
                 filterExcludes: safe { t.filter.excludePatterns as List },
                 commandLine: safe { t.filter.commandLineIncludePatterns as List },
                 classesDirs: safe { t.testClassesDirs.files*.path },
                 tagsIncluded: safe { t.options.includeTags as List }, tagsExcluded: safe { t.options.excludeTags as List }]
            }
            projects << [path: p.path, dir: p.projectDir.path, sourceSets: sets, tests: tests]
        }
        def included = safe { gradle.includedBuilds.collect { [name: it.name, dir: it.projectDir.path] } } ?: []
        emit([event: 'graph', gradle: gradle.gradleVersion, projects: projects, includedBuilds: included])
    } }

    gradle.taskGraph.afterTask { Task task, TaskState state -> guard {
        emit([event: 'task', path: task.path, test: task instanceof Test, executed: state.executed,
              skipMessage: state.skipMessage, failure: state.failure ? why(state.failure) : null])
    } }
}
"""

COMMON_FLAGS = (
    "--no-daemon",
    "--console=plain",
    "--no-build-cache",
    "--no-configuration-cache",
    "--no-configure-on-demand",
    "--continue",
)


# ---- the launcher --------------------------------------------------------------------------------------


def choose_tool(repo: Path) -> list[str]:
    """The command that is Gradle: the repo's own `gradlew` if it has one, else `gradle` on PATH. Raises ValueError for neither.

    The wrapper is preferred because it is the version the repo pins: a Gradle that is too old or too new fails a build for
    no fault of its tests. It runs the repo's own script and jar and may download a distribution on first use, and that is
    the repo's own build. It is only ever run from inside the repo: a `gradlew` that is a link to a file outside it, or not
    a file, is refused, never run.
    """
    wrapper = repo / "gradlew"
    if os.path.lexists(wrapper):
        real = wrapper.resolve()
        if not real.is_file() or not real.is_relative_to(repo):
            raise ValueError(f"{wrapper} is not a file inside the repository (it is {real}): ripple will not run it (pass --test-command)")
        if os.access(real, os.X_OK):
            return [str(real)]
        sh = shutil.which("sh")
        if sh:
            return [sh, str(real)]
    found = shutil.which("gradle")
    if found is None:
        raise ValueError(
            "neither `gradlew` in the repository nor `gradle` on PATH: ripple runs Java tests with Gradle "
            "here (or pass --test-command, for example `./gradlew test`)"
        )
    return [found]


# ---- what Gradle says: the record ----------------------------------------------------------------------


@dataclass
class TestTask:
    path: str
    name: str
    project: str  # the project's path, `:core`
    scheduled: bool
    enabled: bool
    ignore_failures: bool
    classes_dirs: tuple[str, ...]
    narrowing: tuple[str, ...]  # what the build sets that can keep a test from running, named
    command_line: tuple[str, ...]  # the --tests patterns Gradle applied to the task
    tags_only: bool = False  # the narrowing is includeTags/excludeTags and nothing else: the one kind Gradle 9 accepts as why a task ran no test


@dataclass
class Project:
    path: str
    dir: str
    source_sets: list[tuple[str, tuple[str, ...], tuple[str, ...]]]  # (name, java source dirs, classes dirs)
    tests: list[TestTask]


@dataclass
class Event:
    path: str
    test: bool
    executed: bool
    skip: str | None  # UP-TO-DATE, FROM-CACHE, NO-SOURCE, SKIPPED ...: Gradle did not run it
    failure: str | None


@dataclass
class Result:
    """One test as Gradle's listener reported it."""

    task: str
    cls: str
    name: str  # Gradle's name: `addsItems()`, `discounts(int)[1]`
    outcome: str  # SUCCESS, FAILURE, SKIPPED
    ms: int
    assumed: bool  # skipped by a failed assumption
    error: str | None

    @property
    def method(self) -> str:
        return re.split(r"[(\[]", self.name, maxsplit=1)[0]

    @property
    def signature(self) -> str | None:
        """The parameter types in the name (`int` of `discounts(int)[1]`), or None when the name has no parentheses."""
        if "(" not in self.name or ")" not in self.name:
            return None
        return self.name[self.name.index("(") + 1 : self.name.rindex(")")]

    @property
    def ran(self) -> bool:
        return self.outcome != "SKIPPED"


@dataclass
class Suite:
    """A suite Gradle closed: the whole run of a task (level 0), a test JVM (1), a class or a parameterized method (below)."""

    task: str
    name: str
    cls: str | None
    level: int
    outcome: str
    tests: int
    ok: int
    failed: int
    skipped: int
    assumed: bool
    error: str | None = None


@dataclass
class Manifest:
    version: str | None = None
    projects: list[Project] = field(default_factory=list)
    events: dict[str, Event] = field(default_factory=dict)
    results: list[Result] = field(default_factory=list)
    suites: list[Suite] = field(default_factory=list)
    init: bool = False  # the init script ran in the root build
    graph: bool = False  # the task graph was reported: the build got as far as knowing what it would run
    truncated: bool = False  # the file ends inside a line
    oversize: bool = False  # the file is over the cap: only its start was read
    garbled: int = 0  # whole lines that are not a JSON object
    listener_errors: list[str] = field(default_factory=list)  # what ripple's own init script said it failed at
    included: list[tuple[str, str]] = field(default_factory=list)  # (name, directory) of each `gradle.includedBuilds`: their tests are not this build's

    def task(self, path: str) -> TestTask | None:
        return next((t for p in self.projects for t in p.tests if t.path == path), None)

    def root(self, path: str) -> Suite | None:
        return next((s for s in self.suites if s.task == path and s.level == 0), None)


def _strings(value) -> tuple[str, ...]:
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def _bool(value, default=None):
    return value if isinstance(value, bool) else default


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _task(project: str, raw: dict) -> TestTask | None:
    path, name = raw.get("path"), raw.get("name")
    if not isinstance(path, str) or not isinstance(name, str):
        return None
    narrowing = []
    other = False
    for key, text in (
        ("include", "includes"),
        ("exclude", "excludes"),
        ("filterIncludes", "a test filter"),
        ("filterExcludes", "a test filter that excludes"),
        ("tagsIncluded", "includeTags"),
        ("tagsExcluded", "excludeTags"),
    ):
        found = _strings(raw.get(key))
        if found:
            narrowing.append(f"{text} {', '.join(found[:3])}")
            other = other or key not in ("tagsIncluded", "tagsExcluded")
    return TestTask(
        path,
        name,
        project,
        _bool(raw.get("scheduled"), False),
        _bool(raw.get("enabled"), True),
        _bool(raw.get("ignoreFailures"), False),
        _strings(raw.get("classesDirs")),
        tuple(narrowing),
        _strings(raw.get("commandLine")),
        bool(narrowing) and not other,
    )


def read_manifest(path: Path) -> Manifest:
    """The facts the init script wrote, read as the untrusted bytes they are: capped, one JSON line at a time, wrong types dropped.

    A last line with no newline after it is a line the writer did not finish (the init script writes each line whole, with
    its newline, in one write): `truncated`, and that fragment is not read. Past the cap only the start is read and
    `oversize` says so. The first record of a task and the first graph win (see WHAT IS TRUSTED in the module docstring).
    """
    manifest = Manifest()
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_RECORD_BYTES + 1)
    except OSError:
        return manifest
    if len(data) > MAX_RECORD_BYTES:
        manifest.oversize = True
        data = data[:MAX_RECORD_BYTES]
        data = data[: data.rfind(b"\n") + 1]
    elif data and not data.endswith(b"\n"):
        manifest.truncated = True
        data = data[: data.rfind(b"\n") + 1]
    lines = data.decode("utf-8", errors="replace").split("\n")[:-1]
    if len(lines) > MAX_RECORD_LINES:
        manifest.oversize = True
        lines = lines[:MAX_RECORD_LINES]
    for line in lines:
        try:
            raw = json.loads(line)
        except ValueError:
            manifest.garbled += 1
            continue
        if not isinstance(raw, dict):
            manifest.garbled += 1
            continue
        kind = raw.get("event")
        if kind == "init":
            manifest.init = True
        elif kind == "listener_error":
            if len(manifest.listener_errors) < 5:
                manifest.listener_errors.append(clip(str(raw.get("message")), 300))
        elif kind == "graph" and not manifest.graph and isinstance(raw.get("projects"), list):
            manifest.graph = True
            manifest.version = raw["gradle"] if isinstance(raw.get("gradle"), str) else None
            for entry in raw.get("includedBuilds") if isinstance(raw.get("includedBuilds"), list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str) and isinstance(entry.get("dir"), str):
                    manifest.included.append((entry["name"], entry["dir"]))
            for item in raw["projects"]:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("dir"), str):
                    continue
                sets = []
                for entry in item.get("sourceSets") if isinstance(item.get("sourceSets"), list) else []:
                    if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                        sets.append((entry["name"], _strings(entry.get("java")), _strings(entry.get("classes"))))
                tests = [t for t in (_task(item["path"], r) for r in item.get("tests", []) if isinstance(r, dict)) if t]
                manifest.projects.append(Project(item["path"], item["dir"], sets, tests))
        elif kind == "task" and isinstance(raw.get("path"), str):
            skip, failure = raw.get("skipMessage"), raw.get("failure")
            manifest.events.setdefault(
                raw["path"],
                Event(
                    raw["path"],
                    raw.get("test") is True,
                    raw.get("executed") is True,
                    skip if isinstance(skip, str) else None,
                    failure if isinstance(failure, str) else None,
                ),
            )
        elif kind == "test" and isinstance(raw.get("task"), str) and isinstance(raw.get("cls"), str) and isinstance(raw.get("name"), str):
            outcome = raw.get("result")
            if outcome in ("SUCCESS", "FAILURE", "SKIPPED"):
                error = raw.get("error")
                manifest.results.append(
                    Result(raw["task"], raw["cls"], raw["name"], outcome, _int(raw.get("ms")), raw.get("assumed") is True, error if isinstance(error, str) else None)
                )
            else:
                manifest.garbled += 1
        elif kind == "suite" and isinstance(raw.get("task"), str) and isinstance(raw.get("name"), str):
            outcome = raw.get("result")
            cls = raw.get("cls")
            if isinstance(outcome, str):
                manifest.suites.append(
                    Suite(
                        raw["task"],
                        raw["name"],
                        cls if isinstance(cls, str) else None,
                        _int(raw.get("level")),
                        outcome,
                        _int(raw.get("tests")),
                        _int(raw.get("ok")),
                        _int(raw.get("failed")),
                        _int(raw.get("skipped")),
                        raw.get("assumed") is True,
                        raw["error"] if isinstance(raw.get("error"), str) else None,
                    )
                )
    return manifest


def _rel(repo: Path, absolute: str) -> str | None:
    """A path the manifest gave, as repo-relative text ("" for the repo), or None when it is not inside the repo."""
    try:
        target = Path(os.path.realpath(absolute))
    except (OSError, ValueError):
        return None
    if not target.is_relative_to(repo):
        return None
    rel = target.relative_to(repo).as_posix()
    return "" if rel == "." else rel


def _test_roots(repo: Path, project: Project, task: TestTask) -> list[str]:
    """The repo-relative Java source directories of the source set whose classes `task` runs (Gradle's own answer), or [] when no source set owns them."""
    roots: list[str] = []
    for _name, java, classes in project.source_sets:
        if task.classes_dirs and set(task.classes_dirs) <= set(classes):
            roots.extend(r for r in (_rel(repo, d) for d in java) if r is not None)
    return roots


def build_project(repo: Path, manifest: Manifest) -> tuple[jc.Project, dict[str, str]]:
    """The modules of the build as java_check.py reads them (a project with a `test` task, inside the repo), and each one's project path.

    The test source roots of a module are the Java source directories of the source set whose classes the `test` task runs
    (Gradle's own answer, never read from a script). They are used to resolve an id or a path the caller wrote, and to name
    the file of a failure. They are never used to decide what ran.
    """
    modules: list[jc.Module] = []
    paths: dict[str, str] = {}
    for project in manifest.projects:
        task = next((t for t in project.tests if t.name == "test"), None)
        rel = _rel(repo, project.dir)
        if task is None or rel is None:
            continue
        roots = _test_roots(repo, project, task)
        uncertain = not roots
        default = f"{rel}/src/test/java".strip("/")
        module = jc.Module(rel, project.path, tuple(dict.fromkeys(roots)) or (default,), (), uncertain)
        modules.append(module)
        paths[rel] = project.path
    return jc.Project(sorted(modules, key=lambda m: m.dir)), paths


# ---- the request ----------------------------------------------------------------------------------------


def _candidate_roots(repo: Path) -> tuple[str, ...]:
    """Every `src/<set>/java` directory of the repo but `main`: where a test source can be, before Gradle has been asked."""
    found: list[str] = []
    for current, dirs, _files in os.walk(repo):
        rel = Path(current).relative_to(repo)
        parts = rel.parts
        dirs[:] = sorted(
            d for d in dirs if d not in (".git", "node_modules", ".gradle") and not (d == "build" and "src" not in parts)
        )
        if len(parts) >= 3 and parts[-1] == "java" and parts[-3] == "src" and parts[-2] != "main":
            found.append(rel.as_posix())
            dirs[:] = []
    return tuple(found)


def normalize(repo: Path, tests: list[str]) -> list[str]:
    """The tests with each Java id as `impact` hands it out turned into the `file::Class.method(types)` form (as java_check.normalize).

    The id is looked up in the test sources named by directory convention (`src/<set>/java` below the repo, never main),
    and nowhere else: Gradle has not been asked yet, and a path the caller wrote is never searched. That the file is in a
    test source set the build's `test` task runs is proved after, by `parse_wants`, against Gradle's own model.
    """
    result = []
    shim = None
    for given in tests:
        if not isinstance(given, str) or not given or given.startswith(("-", "@")) or "::" in given or jc._control(given):
            result.append(given)
            continue
        if "/" in given or "\\" in given or given.startswith("."):
            result.append(given)
            continue
        match = jc.METHOD_ID.match(given)
        if match is None:
            raise ValueError(f"{given!r} is not a Java test id: expected a.b.Class.method(types), a class or a path")
        qualified, params = match["q"], match["p"]
        parts = qualified.split(".")
        if not all(jc.IDENT_RE.match(p) for p in parts) or (params is not None and not jc._PARAM_CHARS.match(params)):
            raise ValueError(f"{given!r} is not a Java test id: expected a.b.Class.method(types), a class or a path")
        if jc._repo_path(repo, given).exists():
            result.append(given)
            continue
        if len(parts) < 2 and params is None:
            raise ValueError(f"{given!r} is not a Java test id or a path inside {repo}")
        if shim is None:
            shim = jc.Project([jc.Module("", "", _candidate_roots(repo))])
        result.append(jc._resolve_id(repo, shim, given, parts, params))
    return result


def _parse_wants(repo: Path, project: jc.Project, tests: list[str]) -> list[jc.Want]:
    try:
        return jc.parse_wants(repo, project, tests)
    except ValueError as error:
        text = str(error).replace("no module's pom.xml is at or above it", "no project with a `test` task is at or above it")
        text = text.replace("module of the build", "project of the build with a `test` task").replace("pom holds", "build holds")
        raise ValueError(text) from None


# ---- the plan: one Gradle run ----------------------------------------------------------------------------


@dataclass
class Invocation:
    """One Gradle run: the tasks it names, each with its `--tests` patterns, and the wants it answers."""

    tasks: tuple[tuple[str, tuple[str, ...]], ...]
    wants: tuple[jc.Want, ...] = ()
    label: str = ""

    def argv(self, tool: list[str] | None = None, init: str = "<init.gradle>") -> list[str]:
        out = list(tool or ["gradle"]) + list(COMMON_FLAGS) + ["-I", init]
        for task, patterns in self.tasks:
            out.append(task)
            for pattern in patterns:
                out += ["--tests", pattern]
        return out

    def command(self, tool: list[str] | None = None, init: str = "<init.gradle>") -> str:
        return shlex.join(self.argv(tool, init))


def task_path(project: str) -> str:
    return ":test" if project == ":" else f"{project}:test"


def plan(paths: dict[str, str], wants: list[jc.Want]) -> Invocation:
    """The one Gradle run that makes exactly what was asked, and nothing broader.

    With nothing asked: every project's `test`. A project asked for whole is `:proj:test`. A class is `--tests` its binary
    name (Gradle runs its nested classes with it), a method is `--tests Class.method`; the patterns of a project are one
    union in one task, and a pattern that a broader one in the same task already covers is left out (the want is kept, and
    the verdict holds it to its own obligation). Raises ValueError for a project whose path cannot be put in a task name.
    """
    if not wants:
        return Invocation((("test", ()),), (), "the whole build")
    whole = {w.module for w in wants if w.cls is None}
    tasks: list[tuple[str, tuple[str, ...]]] = []
    for module in sorted({w.module for w in wants}):
        project = paths.get(module)
        if project is None or not PROJECT_PATH.match(project):
            raise ValueError(f"the Gradle project of {module or 'the root'!r} ({project!r}) cannot be named in a task: pass --test-command")
        patterns: list[str] = []
        if module not in whole:
            classes = [w.cls for w in wants if w.module == module and w.method is None]
            patterns.extend(dict.fromkeys(classes))
            for want in wants:
                if want.module == module and want.method is not None and not any(
                    want.cls == c or want.cls.startswith(c + "$") for c in classes
                ):
                    # JUnit 4 names a Parameterized invocation `method[1]`, which `--tests Class.method` does not match (measured on 9.9.1:
                    # "No tests found"); `Class.method[*]` beside it does, and Gradle ORs the patterns. A pattern that matches nothing is fine.
                    patterns.extend((f"{want.cls}.{want.method}", f"{want.cls}.{want.method}[*]"))
        tasks.append((task_path(project), tuple(dict.fromkeys(patterns))))
    label = ", ".join(sorted(w.module or "the root" for w in wants))
    return Invocation(tuple(tasks), tuple(wants), label)


# ---- the console ----------------------------------------------------------------------------------------


_COMPILE = re.compile(r"^(?:.*[/\\])?([^/\\\s]+\.java):(\d+): error: (.*)$", re.MULTILINE)
_WENT_WRONG = re.compile(r"^\* What went wrong:\n((?:.+\n?)+)", re.MULTILINE)


@dataclass
class Console:
    compile_errors: list[str]
    went_wrong: list[str]  # the first line of each `What went wrong` block, and the detail under it


def read_console(text: str) -> Console:
    """What Gradle printed that explains a failure that is not a failing test. Used for the words of a problem, never for the verdict."""
    wrong = [clip(m.splitlines()[0].strip(), 200) for m in _WENT_WRONG.findall(text) if m.strip()]
    detail = []
    for block in _WENT_WRONG.findall(text):
        detail.extend(clip(x.lstrip("> ").strip(), 200) for x in block.splitlines()[1:3] if x.strip())
    return Console(
        compile_errors=list(dict.fromkeys(f"{f}:{n} {clip(msg, 120)}" for f, n, msg in _COMPILE.findall(text))),
        went_wrong=list(dict.fromkeys(wrong + detail)),
    )


# ---- the verdict -------------------------------------------------------------------------------------------


@dataclass
class Run:
    invocation: Invocation
    output: str
    exit_code: int | None  # None: it was killed for taking too long
    timed_out: bool = False
    started: bool = True
    manifest: Manifest = field(default_factory=Manifest)


# UP-TO-DATE and FROM-CACHE cannot come out of ripple's own invocation (every Test task is made out of date and uncacheable by
# the init script, and --no-build-cache is passed: tests/test_check_gradle_live.py runs a build twice), so they stay problems:
# they would mean tests did not run in this run, and nothing here may say otherwise.
_SKIP_TEXT = {
    "UP-TO-DATE": "was UP-TO-DATE: Gradle ran no test in this run",
    "FROM-CACHE": "was restored from the build cache: Gradle ran no test in this run",
}


def _reason(failure: str) -> str:
    """Gradle's failure chain without the generic first link ("Execution failed for task ..."), which says nothing the task path does not."""
    parts = failure.split(" <- ")
    if len(parts) > 1 and parts[0].startswith("Execution failed for task"):
        parts = parts[1:]
    return clip(" <- ".join(parts), 200)


def environment_notes(environ: dict[str, str] | None = None) -> list[str]:
    """What around the build can change what `test` runs without being in the repo: init scripts of the user's Gradle home, JVM options."""
    environ = os.environ if environ is None else environ
    notes = []
    home = Path(environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")
    try:
        scripts = sorted(p.name for p in (home / "init.d").iterdir() if p.is_file())
    except OSError:
        scripts = []
    if scripts:
        notes.append(f"{home}/init.d holds init scripts ({', '.join(scripts[:3])}) that apply to every build: they can change what runs, and are not read")
    for name in ("GRADLE_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS"):
        if environ.get(name):
            notes.append(f"${name} is set ({clip(environ[name], 60)}): it applies to the build and its test JVMs, and is not read")
    return notes


def _planned(run: Run) -> list[TestTask]:
    """The `test` tasks the run was to execute: the ones it named, or every `test` task of every project of a whole-build run, scheduled or not."""
    if run.invocation.wants:
        return [t for path, _ in run.invocation.tasks if (t := run.manifest.task(path)) is not None]
    return [t for p in run.manifest.projects for t in p.tests if t.name == "test"]


def _check_task(task: TestTask, run: Run, problems: list[str], notes: list[str]) -> list[Result]:
    """What the record says of one executed `Test` task: its results, after the checks that make the record believable."""
    manifest = run.manifest
    mine = [r for r in manifest.results if r.task == task.path]
    root = manifest.root(task.path)
    if root is None:
        if not run.timed_out:
            problems.append(f"{task.path} has no closing record: Gradle or its test JVM ended before the task finished, so the tests recorded are not all of them")
        return mine
    counts = (
        len(mine),
        sum(r.outcome == "SUCCESS" for r in mine),
        sum(r.outcome == "FAILURE" for r in mine),
        sum(r.outcome == "SKIPPED" for r in mine),
    )
    if counts != (root.tests, root.ok, root.failed, root.skipped):
        problems.append(
            f"{task.path}: the record holds {counts[0]} tests ({counts[1]} passed, {counts[2]} failed, {counts[3]} skipped) but Gradle counted "
            f"{root.tests} ({root.ok} passed, {root.failed} failed, {root.skipped} skipped): the record is incomplete or was altered"
        )
    if root.outcome == "FAILURE" and root.error and not any(r.outcome == "FAILURE" for r in mine):
        problems.append(f"{task.path} failed with no failing test: {_reason(root.error)}")
    unfinished = [
        s
        for s in manifest.suites
        if s.task == task.path and s.outcome == "SKIPPED" and not s.assumed and (s.level == 1 or (s.level > 1 and s.name == s.cls))
    ]
    if unfinished:
        problems.append(
            f"{task.path}: Gradle ended {jc._plural(len(unfinished), 'suite')} SKIPPED without finishing it ({jc._names([s.name for s in unfinished])}): "
            "the test JVM stopped there (System.exit, Runtime.halt), so what ran after it never ran"
        )
    if task.ignore_failures:
        notes.append(f"{task.path} has ignoreFailures set: Gradle exits 0 although a test fails")
    if task.narrowing:
        notes.append(f"{task.path} narrows its tests ({'; '.join(task.narrowing)}): the tests it leaves out did not run")
    if root.tests == 0:
        # Gradle 9 fails a task that finds no test (failOnNoDiscoveredTests); before 9, and with the opt-out, it executes and says nothing.
        # Measured on 9.9.1: only includeTags/excludeTags leave it green. An include or exclude pattern or a filter does not explain it
        # ("test sources present and no filters are applied", "No tests found"), and an exclude that removes every class makes the task
        # NO-SOURCE, never an executed task with no test: so for those the problem stands.
        asked = dict(run.invocation.tasks).get(task.path)
        if task.tags_only:
            notes.append(f"{task.path} executed and ran no test, and the build narrows it by tags")
        elif asked:
            problems.append(
                f"{task.path} executed and ran no test: Gradle matched no test to the `--tests` filter ({', '.join(asked[:4])}), "
                "so nothing that was asked for was checked"
            )
        else:
            problems.append(
                f"{task.path} executed and ran no test: Gradle found none to run in it (a module with JUnit 5 tests and no `useJUnitPlatform()`, "
                "or `failOnNoDiscoveredTests = false`), so nothing in it was checked"
            )
    return mine


def _check_tasks(run: Run, problems: list[str], notes: list[str]) -> list[Result]:
    """Every `test` task the run was to execute needs proof that Gradle ran it now; the results of every Test task that ran."""
    manifest = run.manifest
    whole_build = not run.invocation.wants
    asked = dict(run.invocation.tasks)
    planned = _planned(run)
    if run.invocation.wants:
        for path in asked:
            if manifest.task(path) is None:
                problems.append(f"{path}: not a test task of this build")
    # a Test task the graph holds that the run did not name (the build wired it in, or `test` is a lifecycle task over a Test task
    # of another name, as Android's is) counts like the ones it named
    scheduled = [t for p in manifest.projects for t in p.tests if t.scheduled]
    if not scheduled:
        problems.append("no `test` task was scheduled: the build has no Java test task here, or the tests were excluded (-x test)")
    checked: list[Result] = []
    seen: set[str] = set()
    for task in planned + [t for t in scheduled if t.path not in {p.path for p in planned}]:
        if task.path in seen:
            continue
        seen.add(task.path)
        event = manifest.events.get(task.path)
        if whole_build and not task.scheduled:
            problems.append(f"{task.path} was left out of the task graph (a `-x`, a script of the build, or an init script excludes it): its tests did not run")
            continue
        if event is None:
            if not run.timed_out:
                problems.append(f"{task.path} did not run (Gradle stopped before it, or the build excludes it)")
            continue
        if event.skip is not None:
            # what the build itself deselects is a note, as `exclude` and `excludeTags` are: unless the task was asked for by name
            by_build = event.skip in ("NO-SOURCE", "SKIPPED")
            if by_build and task.path not in asked:
                if event.skip == "NO-SOURCE":
                    notes.append(f"Gradle found no test classes for {task.path} (NO-SOURCE), so no test ran in it")
                else:
                    why = "enabled = false" if not task.enabled else "an onlyIf condition that is false"
                    notes.append(f"{task.path} was skipped by the build (Gradle said {event.skip}: {why}), so its tests did not run")
                if task.narrowing:
                    notes.append(f"{task.path} narrows its tests ({'; '.join(task.narrowing)}): the tests it leaves out did not run")
            else:
                problems.append(f"{task.path} {_SKIP_TEXT.get(event.skip, f'was skipped by Gradle ({event.skip}): it did not run, and it was asked for' if by_build else f'was skipped by Gradle ({event.skip}): Gradle ran no test in it in this run')}")
            continue
        if not event.executed:
            problems.append(f"{task.path} was not executed by Gradle in this run")
            continue
        if task.path in asked and set(task.command_line) != set(asked[task.path]):
            problems.append(f"{task.path}: Gradle applied the filter {sorted(task.command_line)}, not the {sorted(asked[task.path])} asked for")
        checked.extend(_check_task(task, run, problems, notes))
    if whole_build:
        named = {t.path for t in planned}
        for p in manifest.projects:
            for other in p.tests:
                if other.name != "test" and other.path not in named and not other.scheduled:
                    note = f"{other.path} is a test task that `test` does not run"
                    if note not in notes:
                        notes.append(note)
    return checked


# ---- test files Gradle never runs: notes, never a verdict input ---------------------------------------------
#
# The verdict is Gradle's, and Gradle runs only the `test` tasks of the projects of the build. A directory of test files outside that
# is not the repo's tests as `gradle test` means them, and a pass that said nothing about it would read as "the repo's tests pass".
# So each shape is named once, from the task graph and the file system, with no reading of any test and no effect on `passed`.

MAX_LISTED = 5
MAX_DIRS_SCANNED = 20_000
TEST_LANGUAGES = ("java", "kotlin", "groovy")
SOURCE_SUFFIXES = (".java", ".kt", ".groovy", ".scala")
# trees that hold other projects' files, build output or tools, never this repo's test directories
SKIPPED_TREES = {".git", ".gradle", ".idea", "node_modules", "build", "target", "out", "dist", "vendor", "third_party", "resources"}


# `out` and `dist` are usually output, but a module can be called that: pruned unless a build file is in them
OUTPUT_OR_MODULE = {"out", "dist"}


def _has_build_file(directory: Path) -> bool:
    return (directory / "build.gradle").exists() or (directory / "build.gradle.kts").exists()


def _holds_sources(directory: Path) -> bool:
    for seen, (_root, dirs, files) in enumerate(os.walk(directory)):
        if any(f.endswith(SOURCE_SUFFIXES) for f in files):
            return True
        dirs[:] = [d for d in dirs if d not in SKIPPED_TREES]
        if seen >= 500:
            break
    return False


def _test_directories(repo: Path) -> tuple[list[str], bool]:
    """Every `src/test/{java,kotlin,groovy}` below the repo that holds source files, repo-relative, and whether the scan was cut short."""
    found: list[str] = []
    scanned = 0
    for current, dirs, _files in os.walk(repo):
        scanned += 1
        if scanned > MAX_DIRS_SCANNED:
            return found, True
        parts = Path(current).relative_to(repo).parts
        if len(parts) >= 3 and parts[-3:-1] == ("src", "test") and parts[-1] in TEST_LANGUAGES:
            if _holds_sources(Path(current)):
                found.append("/".join(parts))
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_TREES or (d in OUTPUT_OR_MODULE and _has_build_file(Path(current) / d)))
    return found, False


def _named(project_path: str) -> str:
    return "the root project" if project_path == ":" else project_path


def _listed(names: list[str]) -> str:
    return ", ".join(names[:MAX_LISTED]) + (f" and {len(names) - MAX_LISTED} more" if len(names) > MAX_LISTED else "")


def unrun_notes(repo: Path, manifest: Manifest) -> list[str]:
    """One note per shape of test files that `gradle test` does not run: see the comment above. Bounded, and nothing for an ordinary build."""
    notes: list[str] = []
    dirs = {rel: project for project in manifest.projects if (rel := _rel(repo, project.dir)) is not None}
    included = [(name, rel) for name, d in manifest.included if (rel := _rel(repo, d)) is not None]
    if manifest.included:
        names = [name for name, _ in manifest.included]
        notes.append(f"the build includes {_listed(names)} (includeBuild): their tests are not run by this build, and this verdict says nothing about them")
    found, cut = _test_directories(repo)
    strangers: list[str] = []
    for directory in found:
        owner_dir = max((rel for rel in dirs if rel == "" or directory == rel or directory.startswith(rel + "/")), key=len, default=None)
        if any(directory.startswith(rel + "/") for _, rel in included if rel):
            continue  # an included build's tests: named above
        if owner_dir is None:
            continue
        project = dirs[owner_dir]
        language = directory.rsplit("/", 1)[1]
        own = f"{owner_dir}/src/test/{language}".lstrip("/")
        if directory != own:
            strangers.append(directory.split("/", 1)[0] + "/" if directory.startswith("buildSrc/") else directory)
            continue
        task = next((t for t in project.tests if t.name == "test"), None)
        if task is None:
            # whatever its name, a scheduled Test task that reads this directory's classes ran its tests (Android: `test` -> `unitTest`)
            if not any(t.scheduled and directory in _test_roots(repo, project, t) for t in project.tests):
                    notes.append(f"{_named(project.path)} has a test directory ({directory}) and no `test` task, so none of its tests ran")
        elif language == "java":
            roots = _test_roots(repo, project, task)
            if roots and directory not in roots:
                notes.append(
                    f"{_named(project.path)} reads its test classes from {_listed(roots)}, not from {directory}, which holds test files: they did not run"
                )
    if strangers:
        strangers = list(dict.fromkeys(strangers))
        notes.append(
            f"{_listed(strangers)} hold{'s' if len(strangers) == 1 else ''} test files that no project of this build owns "
            "(a project missing from settings.gradle, a sample or a build of its own such as buildSrc): Gradle did not run them as this build's tests"
        )
    if cut:
        notes.append(f"test directories were looked for in the first {MAX_DIRS_SCANNED} directories only")
    return notes


def _check_want(want: jc.Want, results: list[Result], paths: dict[str, str], problems: list[str], notes: list[str]) -> None:
    """What was asked for must have a test of its own that ran: another class's or another project's does not stand in."""
    task = task_path(paths[want.module]) if want.module in paths else None
    here = [r for r in results if r.task == task]
    if want.cls is None:
        subject = f"{want.given}: {want.module or 'the root'}"
        mine = here
    elif want.method is None:
        subject = f"{want.given}: {want.cls.rpartition('.')[2].replace('$', '.')}"
        mine = [r for r in here if r.cls == want.cls or r.cls.startswith(want.cls + "$")]
    else:
        subject = want.given
        mine = [r for r in here if r.cls == want.cls and r.method == want.method]
    if not mine:
        problems.append(f"{subject}: no test ran (Gradle reported none{', though other tests of the class ran' if want.method and any(r.cls == want.cls for r in here) else ''})")
        return
    ran = [r for r in mine if r.ran]
    if not ran:
        problems.append(f"{subject}: skipped or disabled, so it did not run")
        return
    if len(ran) < len(mine):
        notes.append(f"{subject}: {len(mine) - len(ran)} of {len(mine)} skipped")
    if want.method is None:
        return
    signatures = {r.signature for r in ran}
    if want.params is not None:
        wanted = jc._simple(want.params)
        if None in signatures:
            if want.overloads > 1:
                problems.append(f"{subject}({want.params}): Gradle's names carry no parameter types, so the overload cannot be told")
        elif not any(jc._simple(r.signature or "") == wanted for r in ran):
            problems.append(f"{subject}({want.params}): only other overloads ran ({', '.join(sorted(s or '' for s in signatures))})")
    elif want.overloads > 1 and signatures != {None}:
        shown = ", ".join(f"({s})" for s in sorted(s or "" for s in signatures))
        notes.append(f"{subject} is overloaded ({want.overloads} test methods of that name) and Gradle selects by name, so every overload that ran counts: {shown}")


def judge(
    repo: Path, project: jc.Project, paths: dict[str, str], run: Run, command: str, duration: float, timeout: float, environ: dict[str, str] | None = None
) -> dict:
    """The verdict of the run, from the record Gradle's listener wrote and Gradle's exit status. See the module docstring."""
    problems: list[str] = []
    notes: list[str] = []
    failures: list[str] = []
    counts = {"passed": 0, "failed": 0, "errored": 0, "skipped": 0, "classes": 0, "flaky": 0}
    manifest, invocation = run.manifest, run.invocation
    console = read_console(run.output)
    if not run.started:
        problems.append(f"not run: the {timeout:g}s limit was used up before `{invocation.command()}` began")
    if run.timed_out:
        problems.append(f"timed out after {timeout:g}s: Gradle was killed before it finished, so its results are partial")
    if manifest.truncated:
        problems.append("the record of the test run is cut off in the middle of a line, so what follows the cut is unknown")
    if manifest.oversize:
        problems.append("the record of the test run is over the size ripple reads, so it is not all read")
    if manifest.garbled:
        problems.append(f"{jc._plural(manifest.garbled, 'line')} of the record of the test run could not be read")
    for message in manifest.listener_errors:
        problems.append(f"ripple's own init script failed inside Gradle ({message}): its record is incomplete, which says nothing about the tests")
    explained = len(problems)
    if run.started and not manifest.graph and not run.timed_out:
        problems.append(
            "Gradle did not report which tasks it would run (the build failed before its task graph was ready, "
            "or ripple's init script was not applied), so nothing proves a test ran"
            + (": " + "; ".join(console.went_wrong[:2]) if console.went_wrong else "")
        )
    for error in console.compile_errors[:3]:
        problems.append(f"compilation failed: {error}")
    for path, event in manifest.events.items():
        if event.failure is None:
            continue
        if event.test:
            # a Test task fails for its failing tests, and for its JVM (said by its closing record); any other reason is Gradle's to say here
            root = manifest.root(path)
            if any(r.task == path and r.outcome == "FAILURE" for r in manifest.results) or (root is not None and root.error):
                continue
        elif console.compile_errors and "compile" in path.lower():
            continue
        problems.append(f"build failed: task {path}: {_reason(event.failure)}")

    results: list[Result] = []
    if manifest.graph:
        results = _check_tasks(run, problems, notes)
    failing = [r for r in results if r.outcome == "FAILURE"]
    if run.exit_code not in (0, None) and not failing and not run.timed_out and run.started:
        tail = "; ".join(console.went_wrong[:2])
        if len(problems) == explained:
            problems.append(f"gradle exited {run.exit_code} but its record shows no failing test" + (f": {tail}" if tail else ""))
        elif tail and not any(x.level == 0 and x.error for x in manifest.suites) and not any(tail[:60] in p for p in problems):
            problems.append(f"gradle said: {tail}")
    if run.exit_code == 0 and failing:
        notes.append("gradle exited 0 although tests failed (ignoreFailures or the like): the failures are real")

    for r in results:
        counts[{"SUCCESS": "passed", "FAILURE": "failed", "SKIPPED": "skipped"}[r.outcome]] += 1
        if r.outcome == "FAILURE":
            module = next((rel for rel, p in paths.items() if task_path(p) == r.task), "")
            file = jc._file_of(repo, project, module, r.cls)
            ident = f"{file or r.cls}::{r.cls.rpartition('.')[2].replace('$', '.')}.{r.name}"
            failures.append(clip(f"FAILED {ident}" + (f": {r.error}" if r.error else "")))
    counts["classes"] = len({(r.task, r.cls) for r in results})
    if manifest.graph:
        for want in invocation.wants:
            _check_want(want, results, paths, problems, notes)
    notes.extend(n for n in environment_notes(environ) if n not in notes)
    if manifest.graph:
        notes.extend(unrun_notes(repo, manifest))
    ran = counts["passed"] + counts["failed"]
    if run.started and manifest.graph and not run.timed_out and ran == 0 and counts["skipped"] == 0 and len(problems) == explained:
        problems.append("no tests ran: Gradle's listener recorded no test")

    problems = list(dict.fromkeys(problems))
    if counts["skipped"] and ran:
        notes.append(f"{jc._plural(counts['skipped'], 'test')} skipped or disabled, so not run")
    if counts["skipped"] and not ran:
        problems.append("every test that ran was skipped, so nothing was checked")
    # Every way of not passing is a problem or a failure listed above, so that is all the formula asks (a term for each, such as
    # `run.exit_code == 0` or `ran > 0`, was a check no input could reach: tests/test_check_gradle_record.py walks each
    # one, and removing the problem it stands for fails a test). The init line is the exception: a record with no init line
    # is a record the init script did not write, and nothing else says so.
    passed = not problems and not failures and manifest.init
    summary = f"{counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped in {jc._plural(counts['classes'], 'test class')}"
    summary += "; timed out" if run.timed_out else ""
    result = {
        "passed": passed,
        "exit_code": None if run.timed_out else (run.exit_code if run.exit_code is not None else 0),
        "timed_out": run.timed_out,
        "duration_s": round(duration, 2),
        "command": clip(command, MAX_COMMAND),
        "summary": clip(summary),
        "failures": failures[:MAX_FAILURES],
        "failure_count": len(failures),
        "problems": [clip(p, 400) for p in problems[:MAX_PROBLEMS]],
        "counts": counts,
    }
    if len(problems) > MAX_PROBLEMS:
        result["problems_omitted"] = len(problems) - MAX_PROBLEMS
    if notes:
        result["notes"] = [clip(n) for n in dict.fromkeys(notes)]
    return result


# ---- running it ------------------------------------------------------------------------------------------


def init_text(manifest: Path) -> str:
    """The init script with the file it writes to named inside it, as a Groovy string (not passed as a project property the build could read by name)."""
    quoted = str(manifest).replace("\\", "\\\\").replace("'", "\\'")
    return INIT_SCRIPT.replace("@@MANIFEST@@", quoted)


def _write_init(directory: Path, name: str) -> tuple[str, Path]:
    """Write a private init script into a directory, reporting to NAME.jsonl beside it: (the script's path, the record file)."""
    manifest = directory / f"{name}.jsonl"
    path = directory / f"{name}-init.gradle"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(init_text(manifest))
    return str(path), manifest


def _what_happened(output: str) -> str:
    """The lines of a failed `--version` that say why, not the stack frames under them: a `Downloading` line, then the first line that names an exception or error, else the first line."""
    lines = [x.strip() for x in output.splitlines() if x.strip() and not x.lstrip().startswith("at ")]
    if not lines:
        return "no output"
    said = [x for x in lines if x.startswith("Downloading")][:1]
    said += [x for x in lines if ("Exception" in x or "Error" in x) and x not in said][:1]
    return clip("; ".join(said or lines[:1]), 300)


def check_version(repo: Path, tool: list[str], timeout: float) -> str:
    """Ask the Gradle that will run for its version and refuse one older than MIN_GRADLE, naming both. Returns the version text."""
    outcome = run_process(list(tool) + ["--version"], repo, max(timeout, 1.0))
    found = re.search(r"^Gradle (\d+)\.(\d+)(?:\.(\d+))?\S*", outcome.stdout, re.MULTILINE)
    if outcome.timed_out or found is None:
        raise ValueError(
            f"could not read the Gradle version from `{shlex.join(tool)} --version` "
            f"({'timed out' if outcome.timed_out else _what_happened(outcome.stdout)}): ripple will not run tests with an unknown Gradle"
        )
    if (int(found.group(1)), int(found.group(2))) < MIN_GRADLE:
        raise ValueError(
            f"Gradle {found.group(0)[7:]} is older than {MIN_GRADLE[0]}.{MIN_GRADLE[1]}, the oldest Gradle ripple has measured: "
            "pass the command that runs the tests (`--test-command`, for example `./gradlew test`)"
        )
    return found.group(0)[7:]


def discover(repo: Path, tool: list[str], directory: Path, timeout: float) -> Manifest:
    """Ask Gradle for the build's projects and `Test` tasks (the task graph of `help`), so a path or id can be put in a task name."""
    init, manifest = _write_init(directory, "model")
    argv = list(tool) + list(COMMON_FLAGS) + ["-I", init, "--quiet", "help"]
    outcome = run_process(argv, repo, max(timeout, 1.0))
    model = read_manifest(manifest)
    if outcome.timed_out:
        raise ValueError(f"Gradle did not finish reading the build's projects in {timeout:g}s: ripple cannot tell which project a test is in")
    if not model.graph:
        tail = "; ".join(read_console(outcome.stdout).went_wrong[:2]) or clip(outcome.stdout.strip().splitlines()[-1] if outcome.stdout.strip() else "no output", 160)
        raise ValueError(f"Gradle could not read the build's projects (exit {outcome.returncode}): {tail}")
    return model


def run(repo: Path, tests: list[str] | None, timeout: float) -> dict:
    """Run the Java tests asked for (or the whole build's) with Gradle, and judge the record. Raises ValueError for a request it cannot make."""
    repo = repo.resolve()
    check_tests(repo, tests or [])
    if jc.build_tool(repo) != "gradle":
        raise ValueError("no Gradle build file at the repository root")
    tool = choose_tool(repo)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ripple-gradle-") as scratch_name:
        scratch = Path(scratch_name)
        with jc.repo_lock(repo, timeout, "Gradle runs in one build/ directory"):
            check_version(repo, tool, timeout - (time.monotonic() - started))
            if tests:
                model = discover(repo, tool, scratch, timeout - (time.monotonic() - started))
                project, paths = build_project(repo, model)
                wants = _parse_wants(repo, project, tests)
                invocation = plan(paths, wants)
            else:
                invocation = plan({}, [])
            init, manifest_path = _write_init(scratch, "run")
            left = timeout - (time.monotonic() - started)
            if left <= 0:
                result = Run(invocation, "", None, started=False)
                return judge(repo, jc.Project([]), {}, result, invocation.command(tool, init), time.monotonic() - started, timeout)
            outcome = run_process(invocation.argv(tool, init), repo, left)
            manifest = read_manifest(manifest_path)
            project, paths = build_project(repo, manifest)
            result = Run(invocation, outcome.stdout, outcome.returncode, outcome.timed_out, True, manifest)
            command = invocation.command(tool, init)
            return judge(repo, project, paths, result, command, time.monotonic() - started, timeout)
