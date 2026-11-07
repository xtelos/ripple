"""The pinned open-source repositories some eval tasks run over.

eval/repos.json names each one by git URL and exact commit SHA, with the
Python version and test dependencies its own suite needs. Nothing from these
repositories is committed or vendored here: `python -m eval.fetch` downloads
them into a cache directory outside this repository, and everything else
reads them from there.

    $RIPPLE_EVAL_CACHE, else $XDG_CACHE_HOME/ripple-eval, else ~/.cache/ripple-eval
        repos/<name>-<sha12>/    the source at the pinned commit, no .git
        venvs/<name>-<key>/      a virtualenv with the test dependencies, not the repo itself
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

REPOS_FILE = Path(__file__).resolve().parent / "repos.json"
SHA = 40
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")  # a repo name becomes a directory name in the cache
READY_MARKER = ".ripple-ready"


@dataclass(frozen=True)
class RepoSpec:
    name: str
    url: str
    sha: str  # the full 40-hex commit
    describe: str  # a human label such as the release tag; the SHA is what is trusted
    python: str  # interpreter version for the venv, as uv understands it
    pythonpath: tuple[str, ...]  # directories under the repo root to put on sys.path (src layouts)
    tests: str  # the repo's own test directory, relative to its root
    deps: tuple[str, ...]  # exact-version requirements for the test environment
    ignore: tuple[str, ...] = ()  # test files left out of every run (ones that build C code or need the network)
    deselect: tuple[str, ...] = ()  # single tests left out of every run (ones that take a minute)
    unparseable: tuple[str, ...] = ()  # files that do not parse on the untouched pin (test data); empty for all three today

    def env(self, repo: Path, path: str | None = None) -> dict[str, str]:
        """What a test run in this copy of the repo needs on top of the ambient environment.

        The repo is never installed, so PYTHONPATH carries its source directories and its
        root (subprocesses its tests start inherit that, an ini-file pythonpath would not);
        PATH starts with the venv's bin, for tests that start a bare `python`; and
        PYTEST_ADDOPTS leaves out the tests that cannot or should not run. Both the grader
        and the agent's python and pytest commands use the same three, so they agree."""
        path = os.environ.get("PATH", "") if path is None else path
        env = {
            "PYTHONPATH": os.pathsep.join(str(Path(repo) / p) for p in (*self.pythonpath, ".")),
            "PATH": f"{self.venv_python.parent}{os.pathsep}{path}",
        }
        addopts = [f"--ignore={p}" for p in self.ignore] + [f"--deselect={n}" for n in self.deselect]
        if addopts:
            env["PYTEST_ADDOPTS"] = shlex.join(addopts)
        return env

    @property
    def checkout(self) -> Path:
        return cache_root() / "repos" / f"{self.name}-{self.sha[:12]}"

    @property
    def venv(self) -> Path:
        key = hashlib.sha256(json.dumps([self.python, sorted(self.deps)]).encode()).hexdigest()[:8]
        return cache_root() / "venvs" / f"{self.name}-{key}"

    @property
    def venv_python(self) -> Path:
        return self.venv / "bin" / "python"

    @property
    def venv_ready(self) -> Path:
        """Written by eval.fetch once the dependencies are installed. `uv venv` makes the
        interpreter first, so its presence alone does not mean the install finished."""
        return self.venv / READY_MARKER

    @property
    def available(self) -> bool:
        return self.checkout.is_dir() and self.venv_ready.is_file()


def cache_root() -> Path:
    explicit = os.environ.get("RIPPLE_EVAL_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ripple-eval"


def load_repos(path: Path = REPOS_FILE) -> dict[str, RepoSpec]:
    repos = {}
    for name, data in json.loads(path.read_text()).items():
        if not NAME.match(name):
            raise ValueError(f"repos.json: repo name {name!r} must match {NAME.pattern}")
        missing = [k for k in ("url", "sha", "python", "tests", "deps") if k not in data]
        if missing:
            raise ValueError(f"repos.json: {name} is missing {', '.join(missing)}")
        if len(data["sha"]) != SHA or any(c not in "0123456789abcdef" for c in data["sha"]):
            raise ValueError(f"repos.json: {name}.sha must be a full 40-character commit SHA, not a tag or branch")
        repos[name] = RepoSpec(
            name=name,
            url=data["url"],
            sha=data["sha"],
            describe=data.get("describe", ""),
            python=data["python"],
            pythonpath=tuple(data.get("pythonpath", ())),
            tests=data["tests"],
            deps=tuple(data["deps"]),
            ignore=tuple(data.get("ignore", ())),
            deselect=tuple(data.get("deselect", ())),
            unparseable=tuple(data.get("unparseable", ())),
        )
    return repos
