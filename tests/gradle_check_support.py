"""Gradle builds for the `check` tests: real ones for the live tests, and a stand-in for Gradle for the rest.

The projects reuse the Java sources of tests/fixtures/java_check/projects (the Maven runner's fixtures) and write a
Gradle build around them, so a scenario means the same thing for both tools. `Fake` is a process that writes what a
Gradle run writes (the record of ripple's init script: the task graph, one line per test and per suite, the outcome of every
task, and a console), shaped after what real Gradle 9.9.1 recorded in the captures and the live tests, so the verdict logic
is tested where no Gradle exists.
"""

from __future__ import annotations

import glob
import json
import re
import shutil
from pathlib import Path

from java_check_support import PROJECTS

from ripple import checker, gradle_check

JUNIT = """
dependencies {
  testImplementation platform('org.junit:junit-bom:5.11.3')
  testImplementation 'org.junit.jupiter:junit-jupiter'
  testRuntimeOnly 'org.junit.platform:junit-platform-launcher'
}
"""
BUILD = "plugins { id 'java' }\nrepositories { mavenCentral() }\n" + JUNIT + "test { useJUnitPlatform() }\n"


def find_gradle() -> str | None:
    """A Gradle binary: on PATH, else the portable copy the measurements used under /tmp/gradle-toolchain."""
    return shutil.which("gradle") or next(iter(sorted(glob.glob("/tmp/gradle-toolchain/root/gradle-*/bin/gradle"))), None)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def project(tmp: Path, source: str, build: str = BUILD, settings: str = "rootProject.name = 'shop'\n", offline: bool = True) -> Path:
    """A Gradle build around the sources of the Maven fixture `source`: build file, settings and (offline) gradle.properties."""
    repo = tmp / "repo"
    shutil.copytree(PROJECTS / source, repo, ignore=shutil.ignore_patterns("pom.xml", "target"))
    write(repo / "build.gradle", build)
    write(repo / "settings.gradle", settings)
    if offline:
        write(repo / "gradle.properties", "org.gradle.offline=true\n")
    return repo.resolve()


def multi(tmp: Path, offline: bool = True) -> Path:
    """Two projects, :core and :web (web depends on core), from the Maven fixture multimod_ok."""
    repo = tmp / "repo"
    shutil.copytree(PROJECTS / "multimod_ok", repo, ignore=shutil.ignore_patterns("pom.xml", "target"))
    write(repo / "settings.gradle", "rootProject.name = 'root'\ninclude 'core', 'web'\n")
    write(repo / "build.gradle", "")
    write(repo / "core" / "build.gradle", BUILD)
    write(repo / "web" / "build.gradle", BUILD.replace("dependencies {", "dependencies {\n  implementation project(':core')", 1))
    if offline:
        write(repo / "gradle.properties", "org.gradle.offline=true\n")
    return repo.resolve()


# ---- the record, as the init script writes it -----------------------------------------------------------


def graph(repo: Path, projects: dict[str, str] | None = None, scheduled=True, extra_tasks: list[dict] | None = None, **task) -> str:
    """The `graph` line for a build whose projects are {path: relative dir} (default: the root alone)."""
    projects = projects or {":": ""}
    items = []
    for path, rel in projects.items():
        base = str(repo / rel) if rel else str(repo)
        test_path = ":test" if path == ":" else f"{path}:test"
        entry = {
            "name": "test",
            "path": test_path,
            "enabled": True,
            "ignoreFailures": False,
            "scheduled": scheduled,
            "include": [],
            "exclude": [],
            "filterIncludes": [],
            "filterExcludes": [],
            "commandLine": [],
            "classesDirs": [f"{base}/build/classes/java/test"],
            "tagsIncluded": [],
            "tagsExcluded": [],
        }
        entry.update(task)
        items.append(
            {
                "path": path,
                "dir": base,
                "sourceSets": [
                    {"name": "main", "java": [f"{base}/src/main/java"], "classes": [f"{base}/build/classes/java/main"]},
                    {"name": "test", "java": [f"{base}/src/test/java"], "classes": [f"{base}/build/classes/java/test"]},
                ],
                "tests": [entry] + (extra_tasks or []),
            }
        )
    return json.dumps({"event": "graph", "gradle": "9.9.1", "projects": items})


def init_line() -> str:
    return json.dumps({"event": "init", "gradle": "9.9.1"})


def task_line(path: str, skip: str | None = None, failure: str | None = None, test: bool = True, executed: bool = True) -> str:
    return json.dumps({"event": "task", "path": path, "test": test, "executed": executed, "skipMessage": skip, "failure": failure})


def case_line(task: str, cls: str, name: str, result: str = "SUCCESS", ms: int = 2, assumed: bool = False, error: str | None = None) -> str:
    return json.dumps({"event": "test", "task": task, "cls": cls, "name": name, "result": result, "ms": ms, "assumed": assumed, "error": error})


def suite_line(
    task: str, name: str, level: int, result: str, tests: int, ok: int, failed: int = 0, skipped: int = 0, cls: str | None = None,
    assumed: bool = False, error: str | None = None,
) -> str:
    return json.dumps(
        {"event": "suite", "task": task, "name": name, "cls": cls, "level": level, "result": result, "tests": tests, "ok": ok,
         "failed": failed, "skipped": skipped, "ms": 5, "assumed": assumed, "error": error}
    )


def listener(task: str, classes: dict[str, list[tuple[str, str]]], executor: str = "SUCCESS", closing: bool = True, class_result: dict[str, str] | None = None) -> list[str]:
    """What Gradle's listener says of one `Test` task: `classes` is {binary name: [(test name, SUCCESS|FAILURE|SKIPPED), ...]}.

    Every number is derived from the tests, as Gradle derives its own. `executor` is the outcome of the test JVM's own suite
    (SKIPPED: the JVM stopped inside it), `class_result` overrides a class suite's outcome, `closing` False leaves out the
    JVM's and the run's closing records (a record that was cut off before them).
    """
    out: list[str] = []
    every: list[str] = []
    for cls, tests in classes.items():
        for name, result in tests:
            out.append(case_line(task, cls, name, result, assumed=False))
            every.append(result)
        got = [r for _, r in tests]
        outcome = (class_result or {}).get(cls) or ("FAILURE" if "FAILURE" in got else "SUCCESS")
        out.append(suite_line(task, cls, 2, outcome, len(got), got.count("SUCCESS"), got.count("FAILURE"), got.count("SKIPPED"), cls))
    if closing:
        total = (len(every), every.count("SUCCESS"), every.count("FAILURE"), every.count("SKIPPED"))
        failing = "FAILURE" if total[2] else "SUCCESS"
        out.append(suite_line(task, "Gradle Test Executor 1", 1, "FAILURE" if total[2] else executor, *total))
        out.append(suite_line(task, f"Gradle Test Run {task}", 0, failing, *total))
    return out


OK_CONSOLE = "> Task :test\n\nBUILD SUCCESSFUL in 3s\n"
SHOP = {
    "com.acme.shop.CartTest": [("addsItems()", "SUCCESS"), ("discounts(int)[1]", "SUCCESS"), ("discounts(int)[2]", "SUCCESS")],
    "com.acme.shop.CartTest$Receipts": [("receiptHasTotal()", "SUCCESS")],
    "com.acme.shop.CartTests": [("extraWorks()", "SUCCESS")],
}


def record(repo: Path, classes: dict[str, list[tuple[str, str]]] | None = None, **options) -> list[str]:
    """A whole record of a clean run of the root project's `test`: init, graph, the listener's lines, the task's outcome."""
    return [init_line(), graph(repo), *listener(":test", SHOP if classes is None else classes, **options), task_line(":test")]


class Fake:
    """A Gradle that writes what a run writes. `script(argv, repo, manifest)` is called with the run's argv and returns (exit_code, console) or (exit_code, console, timed_out)."""

    def __init__(self, script):
        self.script = script
        self.calls: list[list[str]] = []

    def __call__(self, argv, cwd, timeout, merge_stderr=True):
        self.calls.append(list(argv))
        init = Path(argv[argv.index("-I") + 1])  # the record is named inside the init script, not passed on the command line
        manifest = re.search(r"def sink = '(.*)'", init.read_text()).group(1)
        outcome = self.script(argv, Path(cwd), Path(manifest))
        code, console = outcome[:2]
        return checker.Process(console, "", code, len(outcome) > 2 and bool(outcome[2]))


def install(monkeypatch, script, tool: str = "/usr/bin/gradle") -> Fake:
    """Replace the process runner and the launcher of gradle_check with a Fake running `script`."""
    fake = Fake(script)
    monkeypatch.setattr(gradle_check, "run_process", fake)
    monkeypatch.setattr(gradle_check.shutil, "which", lambda name: tool if name == "gradle" else None)
    return fake


def lines(manifest: Path, *rows: str) -> None:
    with open(manifest, "a", encoding="utf-8") as handle:
        handle.writelines(row + "\n" for row in rows)


def run_with(monkeypatch, tmp_path: Path, rows, exit_code: int = 0, console: str = OK_CONSOLE, source: str = "shop", tests=None, raw: str | None = None, timeout: float = 60) -> dict:
    """check on a Gradle project whose Gradle writes `rows` (a function of the repo, or a list) to its record, or `raw` bytes, and exits `exit_code`."""
    from ripple.profile import JAVA

    repo = project(tmp_path, source)

    def script(argv, cwd, manifest):
        if raw is not None:
            manifest.write_text(raw)
        else:
            lines(manifest, *(rows(cwd) if callable(rows) else rows))
        return exit_code, console

    install(monkeypatch, script)
    return checker.run_check(repo, tests=tests, timeout=timeout, profile=JAVA)


# ---- runs recorded from real Gradle ----------------------------------------------------------------------


CAPTURES = Path(__file__).resolve().parent / "fixtures" / "gradle_check" / "captures"


def capture_names() -> list[str]:
    return sorted(p.name for p in CAPTURES.iterdir() if p.is_dir())


def capture_repo(tmp: Path, name: str) -> Path:
    """The project of a capture (tests/fixtures/gradle_check/capture.py), as it was when the real Gradle ran."""
    repo = tmp / "repo"
    shutil.copytree(CAPTURES / name / "project", repo)
    return repo.resolve()


def replay(name: str):
    """A Gradle that does, call by call, what the real one did in the capture: its record, its console, its exit status."""
    base = CAPTURES / name
    calls = sorted(int(p.stem) for p in (base / "calls").glob("*.kind"))
    state = {"next": 0}

    def script(argv, cwd, manifest):
        number = calls[state["next"]]
        state["next"] += 1
        kind = (base / "calls" / f"{number}.kind").read_text()
        assert kind == ("model" if "help" in argv else "run"), (name, number, kind, argv)
        here = str(cwd)
        write(manifest, (base / "calls" / f"{number}.manifest").read_text().replace("@REPO@", here))
        return int((base / "calls" / f"{number}.exit").read_text()), (base / "calls" / f"{number}.console").read_text().replace("@REPO@", here)

    return script
