"""The agent's side of a task over a node repository (vitest or jest): what eval/run.py needs beyond the Python path.

The Python path gives the agent `python`, `python3` and `pytest` shims and lets it run pytest, nothing else. This
is the same closed design for node:

- The shell holds exactly three commands, each a shim in the run's own bin directory: the repo's runner (`vitest`
  or `jest`) and `tsc`. Not `npm test` (see below), not `npx`, `npm run`, `npm exec` or `node`: each can run any
  package script or any code, so each is a rewrite channel (sed, xargs and `node -e` all come in through them).
- The runner shim runs what the grader runs, `RepoSpec.node_argv` in the same environment (the repo's own
  binaries first on PATH, CI=true, no colour), so the agent's pass count is the grader's pass count. With no
  arguments it runs the repo's whole suite exactly as the grader does; with arguments (file paths, a -t filter)
  they go after the same fixed flags. Flags that write files or hold the shell open are refused: snapshot
  updates, report files, coverage output, watch mode, `init`.
- `tsc` is let through for the type check only: `--noEmit` is forced and only `-p/--project` and a few read-only
  flags are accepted, so it cannot emit, build, init a config or write a build-info file.
- `npm test` is not offered because the repos' own scripts differ and some write: superstruct's runs `npm run
  build` (rollup, writes dist/) first, tsyringe's runs `yarn lint`, neverthrow's runs the type check after
  vitest, and none passes --no-cache (vitest before 4 crashes with EACCES on the read-only node_modules).

The same file holds the readiness checks that run before any agent starts (node and the runner's binaries are
there; with ripple, an interpreter that can index TypeScript and a server that speaks MCP for the repo) and the
code-file view that `changed_files` and the diff use for a node task.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .repos import RepoSpec

# What a node task's `changed_files` and diff look at: the sources the grader treats as code (check_node.SOURCE_EXT,
# which covers .d.ts), JSON (package.json, tsconfig) and the docs that renames touch (task 28 edits .md and .mdx).
NODE_CODE = re.compile(r"\.(?:[cm]?[jt]sx?|json|mdx?)$")
SKIPPED_DIRS = frozenset({"node_modules", "__pycache__"})
RIPPLE_TOOLS = frozenset({"callers", "callees", "impact", "path", "check"})
PREFLIGHT_SYMBOL = "ripple_preflight_no_such_symbol"
IMPORTS = "import ripple, tree_sitter, tree_sitter_typescript"


def node_files(root: Path) -> dict[str, Path]:
    """Every code file under root by relative path, for diff_trees. node_modules is never entered (in a working
    copy it is a link to the shared, read-only install), nor are dot directories."""
    found = {}
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not d.startswith("."))
        for name in files:
            path = Path(here) / name
            if NODE_CODE.search(name):
                found[str(path.relative_to(root))] = path
    return found


# ----------------------------------------------------------------------------------------- the prompt


def runner_phrase(spec: RepoSpec) -> str:
    return "vitest run" if spec.runner == "vitest" else "jest"


def node_preamble(spec: RepoSpec) -> str:
    """The shared note on the shell for a node task: the Python PREAMBLE's two sentences, naming the real commands."""
    return (
        f"The shell is there for running the tests (`{runner_phrase(spec)}`, optionally with file paths) and the type "
        "checker (`tsc --noEmit -p <tsconfig>`): do not use it to edit files (sed, scripted rewrites, node -e). "
        "Make every edit with the Edit or Write tool."
    )


def node_allowed_tools(spec: RepoSpec) -> list[str]:
    """The permission rules for the agent's shell: the repo's runner and tsc, which are the shims."""
    return [f"Bash({spec.runner}:*)", "Bash(tsc:*)"]


# ------------------------------------------------------------------------------------------- the shims

VITEST_REFUSED_FIRST = "init|watch|dev|bench|list|related|typecheck"
VITEST_REFUSED = (
    "-u|--update|--update=*|-w|--watch|--watch=*|--outputFile|--outputFile=*|--outputFile.*|--coverage|--coverage.*"
    "|--ui|--open|--browser|--browser.*"
)
JEST_REFUSED = (
    "-u|--updateSnapshot|--updateSnapshot=*|--init|--watch|--watch=*|--watchAll|--watchAll=*|--outputFile|--outputFile=*"
    "|--coverage|--coverage=*|--collectCoverage|--collectCoverage=*|--coverageDirectory|--coverageDirectory=*"
)
# tsc: a positive list. -p/--project takes a value; the rest are flags that read and print.
TSC_ALLOWED = "--noEmit|--pretty|--noErrorTruncation|--listFiles|--showConfig|--version|-v|--help|-h|--skipLibCheck"


QUOTED_FIRST, QUOTED_ARG = "'$first'", "'$a'"  # the refused word, inside the shim's double-quoted message


def refuse(name: str, what: str) -> str:
    """A shell command that says what was refused and stops with status 2. `what` is shell text (a quoted variable)."""
    return (
        f'{{ echo "{name}: {what} is not available in this environment '
        f'(the shell may only run the tests and the type check)" >&2; exit 2; }}'
    )


def shim_env_lines(repo: Path, spec: RepoSpec) -> str:
    """What the grader's runner_env sets, for the shim: the repo's own binaries first on PATH (RepoSpec.env), CI and no colour."""
    bin_dir = spec.env(repo, path="$PATH")["PATH"].split(os.pathsep)[0]
    return f'export PATH={shlex.quote(bin_dir)}:"$PATH"\nexport CI=true NO_COLOR=1 FORCE_COLOR=0'


def runner_shim(repo: Path, spec: RepoSpec) -> str:
    fixed = spec.node_argv(targets=[])  # vitest run --no-cache [--config C] [--exclude G]; jest [--config C]
    defaults = spec.node_argv()[len(fixed) :]  # the repo's own tests directory, as the grader runs it
    if spec.runner == "jest":
        # --ci for the same reason CI=true is set; --watchman=false is what the grader passes, since a missing or
        # blocked watchman would hang the crawl. A repo-level ignore list is applied by node_argv to the default
        # target only (jest's own flag would replace the config's list), so it does not reach a path given by hand.
        fixed = [*fixed, "--ci", "--watchman=false"]
    real = Path(repo) / "node_modules" / ".bin" / fixed[0]
    command = " ".join(shlex.quote(a) for a in [str(real), *fixed[1:]])
    lines = [
        "#!/bin/sh",
        "# Written by eval/node_agent.py: runs what the grader runs (RepoSpec.node_argv), and refuses what writes files.",
        shim_env_lines(repo, spec),
        'first="${1-}"',
    ]
    if spec.runner == "vitest":
        lines.append(f'case "$first" in {VITEST_REFUSED_FIRST}) {refuse(spec.runner, QUOTED_FIRST)} ;; esac')
    refused = VITEST_REFUSED if spec.runner == "vitest" else JEST_REFUSED
    lines.append(f'for a in "$@"; do case "$a" in {refused}) {refuse(spec.runner, QUOTED_ARG)} ;; esac; done')
    if spec.runner == "vitest":
        lines.append('if [ "$first" = run ]; then shift; fi')
    lines.append(f'if [ "$#" -eq 0 ]; then set -- {" ".join(shlex.quote(d) for d in defaults)}; fi')
    lines.append(f'exec {command} "$@"')
    return "\n".join(lines) + "\n"


def tsc_shim(repo: Path, spec: RepoSpec) -> str:
    real = Path(repo) / "node_modules" / ".bin" / "tsc"
    no = refuse("tsc", QUOTED_ARG)
    return "\n".join(
        [
            "#!/bin/sh",
            "# Written by eval/node_agent.py: the type check only. --noEmit is forced and only -p/--project and read-only flags pass.",
            shim_env_lines(repo, spec),
            "n=$#",
            'while [ "$n" -gt 0 ]; do',
            '  a="$1"; shift; n=$((n - 1))',
            '  case "$a" in',
            f'    {TSC_ALLOWED}) set -- "$@" "$a" ;;',
            f'    -p|--project) if [ "$n" -eq 0 ]; then {no}; fi; v="$1"; shift; n=$((n - 1)); set -- "$@" "$a" "$v" ;;',
            f"    *) {no} ;;",
            "  esac",
            "done",
            f'exec {shlex.quote(str(real))} --noEmit "$@"',
        ]
    ) + "\n"


def write_node_shims(bin_dir: Path, spec: RepoSpec, repo: Path) -> None:
    """The runner and tsc for the agent of one run, shell scripts that name this run's copy of the repo."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, text in ((spec.runner, runner_shim(repo, spec)), ("tsc", tsc_shim(repo, spec))):
        shim = bin_dir / name
        shim.write_text(text)
        shim.chmod(0o755)


# ------------------------------------------------------------------------------------- readiness checks


def node_problems(spec: RepoSpec) -> list[str]:
    """What is missing for an agent to run this repo's tests, before any agent starts."""
    problems = []
    if not shutil.which("node"):
        problems.append("node is not on PATH")
    bin_dir = spec.node_modules / ".bin"
    for name in (spec.runner, "tsc"):
        if not (bin_dir / name).exists():
            problems.append(f"{spec.name}: {name} is not in the installed dependencies ({bin_dir}); run `python -m eval.fetch`")
    return problems


def import_problem(python: str, timeout: float = 60) -> str | None:
    """Why this interpreter cannot run ripple on TypeScript, or None. It must import ripple, tree_sitter and
    tree_sitter_typescript (ripple's `typescript` extra); the child is isolated (-I) as the server is."""
    try:
        done = subprocess.run([python, "-I", "-c", IMPORTS], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"{python} could not be run: {error}"
    if done.returncode:
        tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
        return (
            f"{python} cannot index TypeScript ({tail}). Name an interpreter that has ripple with the typescript extra "
            f"(pip install 'ripple-code[typescript]') with --ripple-python"
        )
    return None


class Handshake:
    """The MCP server as the agent's client meets it: JSON-RPC lines over stdio, with a timeout on every reply."""

    def __init__(self, entry: dict, cwd: Path):
        env = {**os.environ, **entry.get("env", {})}
        self.proc = subprocess.Popen(
            [entry["command"], *entry.get("args", [])],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.lines: queue.Queue = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines.put), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, self.stderr.append), daemon=True).start()
        self.next_id = 0

    @staticmethod
    def _pump(stream, sink) -> None:
        for line in stream:
            sink(line)

    def request(self, method: str, params: dict | None, timeout: float):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        while True:
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f"no reply to {method} in {timeout:g}s") from None
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == self.next_id:
                return message

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()


def handshake_problems(entry: dict, cwd: Path, timeout: float = 120) -> list[str]:
    """Start the server exactly as the agent's config does and check what an agent would get: the five ripple tools
    are listed, and a query over the repo comes back without a `warnings` list (a warning means part of the repo,
    here the TypeScript, was left out of the index)."""
    try:
        client = Handshake(entry, cwd)
    except OSError as error:
        return [f"the ripple server could not be started ({entry['command']}): {error}"]
    try:
        try:
            client.request(
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "eval-preflight", "version": "0"}},
                timeout,
            )
            client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            listed = client.request("tools/list", None, timeout)
            names = {t.get("name") for t in (listed.get("result") or {}).get("tools", [])}
            if names < RIPPLE_TOOLS or not names:
                missing = ", ".join(sorted(RIPPLE_TOOLS - names)) or "all of them"
                return [f"the ripple server's handshake does not list its tools (missing: {missing}; listed: {sorted(names) or 'none'})"]
            called = client.request("tools/call", {"name": "callers", "arguments": {"symbol": PREFLIGHT_SYMBOL}}, timeout)
        except TimeoutError as error:
            tail = "".join(client.stderr)[-300:].strip()
            return [f"the ripple server did not answer ({error})" + (f": {tail}" if tail else "")]
        result = called.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
        try:
            answer = json.loads(text)
        except ValueError:
            return [f"the ripple server's answer to a query was not JSON: {text[:200]!r}"]
        if answer.get("warnings"):
            return [f"the ripple server indexed only part of the repository: {answer['warnings']}"]
        return []
    finally:
        client.close()


def ripple_problems(tasks, ripple_python: str, make_entry, copy_fixture) -> list[str]:
    """The with-ripple arm's readiness for the node repos of `tasks`: the interpreter can index TypeScript, and for
    each repo the server (launched through make_entry, the same config the runs use) lists its tools and answers a
    query on a scratch copy of the repo. Returns the problems, empty when the arm can run."""
    problem = import_problem(ripple_python)
    if problem:
        return [problem]
    problems = []
    seen = set()
    for task in tasks:
        if not task.is_node or task.spec.name in seen:
            continue
        seen.add(task.spec.name)
        with tempfile.TemporaryDirectory(prefix="ripple-eval-preflight-") as scratch:
            root = Path(scratch)
            repo = copy_fixture(task, root / "repo")
            write_node_shims(root / "bin", task.spec, repo)
            entry = make_entry(task, repo, root / "cache", root / "bin")
            problems += [f"{task.spec.name}: {p}" for p in handshake_problems(entry, repo)]
    return problems
