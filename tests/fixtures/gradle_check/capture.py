"""Record a real Gradle run made through ripple's Gradle runner, so a test can replay it where no Gradle exists.

usage: python tests/fixtures/gradle_check/capture.py <name> <project-dir> [test id ...]

It needs a JDK and a Gradle (on PATH, JAVA_HOME and GRADLE_USER_HOME set as for tests/test_check_gradle_live.py) and a
project whose dependencies Gradle can resolve (offline from that home is fine). It runs `gradle_check.run` on the project
with the real Gradle and keeps, under captures/<name>/:

  project/            the project's files (no build output, no .gradle), so a replay has the same sources
  calls/N.kind        "model" (the `help` run that finds the projects, only when ids were asked for) or "run"
  calls/N.manifest    the record ripple's init script wrote (the graph, a line per test and per suite, the task outcomes),
                      with the project's path replaced by @REPO@
  calls/N.console     what Gradle printed, the same replacement
  calls/N.exit        Gradle's exit status
  meta.json           the request, and the verdict ripple gave when it was recorded (for reading, and for the replay test)

tests/gradle_check_support.py `replay` writes these back, in order, as the Gradle a test runs against.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from ripple import checker, gradle_check


def main(name: str, source: str, tests: list[str]) -> dict:
    out = HERE / "captures" / name
    if out.exists():
        shutil.rmtree(out)
    (out / "calls").mkdir(parents=True)
    scratch = Path(tempfile.mkdtemp(prefix="ripple-capture-"))
    repo = (scratch / "repo").resolve()
    shutil.copytree(source, repo, ignore=shutil.ignore_patterns("build", ".gradle", "local-build-cache"))
    here = str(repo)
    calls: list[dict] = []
    real = checker.run_process

    def recording(argv, cwd, timeout, merge_stderr=True):
        if argv[-1] == "--version":  # asked before anything runs; the replay answers it itself
            return real(argv, cwd, timeout, merge_stderr)
        init = Path(argv[argv.index("-I") + 1])
        manifest = Path(re.search(r"def sink = '(.*)'", init.read_text()).group(1))
        kind = "model" if "help" in argv else "run"
        outcome = real(argv, cwd, timeout, merge_stderr)
        number = len(calls)
        text = manifest.read_text() if manifest.exists() else ""
        (out / "calls" / f"{number}.kind").write_text(kind)
        (out / "calls" / f"{number}.manifest").write_text(text.replace(here, "@REPO@"))
        (out / "calls" / f"{number}.console").write_text(outcome.stdout.replace(here, "@REPO@"))
        (out / "calls" / f"{number}.exit").write_text(str(outcome.returncode))
        calls.append({"kind": kind, "exit": outcome.returncode})
        return outcome

    gradle_check.run_process = recording
    request = gradle_check.normalize(repo, list(tests)) if tests else []
    result = gradle_check.run(repo, request, 1500)
    shutil.copytree(repo, out / "project", ignore=shutil.ignore_patterns("build", ".gradle", "local-build-cache"))
    (out / "meta.json").write_text(
        json.dumps(
            {"request": list(tests), "calls": calls, "recorded_verdict": {k: result[k] for k in ("passed", "summary", "problems", "failures", "notes") if k in result}},
            indent=2,
        )
        + "\n"
    )
    shutil.rmtree(scratch, ignore_errors=True)
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    print(json.dumps(main(sys.argv[1], sys.argv[2], sys.argv[3:]), indent=1))
