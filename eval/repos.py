"""The pinned open-source repositories some eval tasks run over.

eval/repos.json names each one by git URL and exact commit SHA, with what its
own suite needs: for a Python repo the interpreter version and pinned test
dependencies, for a node repo ("runtime": "node") the test runner (vitest or jest)
and a package-lock.json kept in eval/lockfiles/<name>/. Nothing from these
repositories is committed or vendored here: `python -m eval.fetch` downloads
them into a cache directory outside this repository, and everything else
reads them from there.

    $RIPPLE_EVAL_CACHE, else $XDG_CACHE_HOME/ripple-eval, else ~/.cache/ripple-eval
        repos/<name>-<sha12>/    the source at the pinned commit, no .git
        venvs/<name>-<key>/      python repos: a virtualenv with the test dependencies, not the repo itself
        deps/<name>-<key>/       node repos: node_modules from `npm ci --ignore-scripts`, read-only;
                                 the key is a hash of the lockfile's content

A working copy of a node repo gets node_modules as a symlink to deps/<name>-<key>/node_modules
(link_deps), never a copy.
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
RUNTIMES = ("python", "node")
RUNNERS = ("vitest", "jest")
PYTHON_ONLY_KEYS = ("python", "pythonpath", "deps", "deselect", "unparseable")
NODE_ONLY_KEYS = ("manager", "runner", "lockfile", "config")


class WrongRuntime(ValueError):
    """A property that belongs to one runtime was asked of a repo that has the other."""


class NotPythonRepo(WrongRuntime):
    """The venv, its interpreter or its marker, asked of a node repo."""


class NotNodeRepo(WrongRuntime):
    """The deps directory, node_modules, the runner command, asked of a python repo."""


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
    # Node repos only. python, pythonpath, deps, deselect and unparseable stay empty for them, and
    # `ignore` is read per runner (see node_argv).
    runtime: str = "python"  # "python" or "node"
    manager: str = ""  # the package manager; npm is the only one
    runner: str = ""  # "vitest" or "jest"
    lockfile: Path | None = None  # absolute path of the vendored package-lock.json (eval/lockfiles/<name>/)
    config: str = ""  # runner config file relative to the repo root, where the runner must be told (tsyringe's jest)

    def env(self, repo: Path, path: str | None = None) -> dict[str, str]:
        """What a test run in this copy of the repo needs on top of the ambient environment.

        The repo is never installed, so PYTHONPATH carries its source directories and its
        root (subprocesses its tests start inherit that, an ini-file pythonpath would not);
        PATH starts with the venv's bin, for tests that start a bare `python`; and
        PYTEST_ADDOPTS leaves out the tests that cannot or should not run. Both the grader
        and the agent's python and pytest commands use the same three, so they agree.

        A node repo is never built either: its node_modules is a link to the cached install
        (link_deps), and the one thing a run needs is that link's .bin first on PATH, so a
        bare `vitest` or `jest` is the pinned one."""
        path = os.environ.get("PATH", "") if path is None else path
        if self.is_node:
            return {"PATH": f"{Path(repo) / 'node_modules' / '.bin'}{os.pathsep}{path}"}
        env = {
            "PYTHONPATH": os.pathsep.join(str(Path(repo) / p) for p in (*self.pythonpath, ".")),
            "PATH": f"{self.venv_python.parent}{os.pathsep}{path}",
        }
        addopts = [f"--ignore={p}" for p in self.ignore] + [f"--deselect={n}" for n in self.deselect]
        if addopts:
            env["PYTEST_ADDOPTS"] = shlex.join(addopts)
        return env

    @property
    def is_node(self) -> bool:
        return self.runtime == "node"

    def _require_python(self) -> None:
        if self.runtime != "python":
            raise NotPythonRepo(f"{self.name} is a {self.runtime} repo, it has no python environment")

    def _require_node(self) -> None:
        if self.runtime != "node":
            raise NotNodeRepo(f"{self.name} is a {self.runtime} repo, it has no node dependencies")

    @property
    def checkout(self) -> Path:
        return cache_root() / "repos" / f"{self.name}-{self.sha[:12]}"

    @property
    def venv(self) -> Path:
        self._require_python()
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
    def deps_dir(self) -> Path:
        """Where a node repo's installed packages live, outside the checkout. The key is the
        lockfile's content (and the package manager): a changed lockfile is a new directory and
        the old one is left alone. It does not need the checkout, which may not exist yet."""
        self._require_node()
        digest = hashlib.sha256(self.lockfile.read_bytes()).hexdigest()
        key = hashlib.sha256(json.dumps([self.manager, digest]).encode()).hexdigest()[:8]
        return cache_root() / "deps" / f"{self.name}-{key}"

    @property
    def node_modules(self) -> Path:
        return self.deps_dir / "node_modules"

    @property
    def deps_ready(self) -> Path:
        """Written by eval.fetch after `npm ci` finished and the tree was made read-only. A
        node_modules directory alone does not mean the install finished."""
        return self.deps_dir / READY_MARKER

    @property
    def lockfile_in_repo(self) -> Path:
        """Where `npm ci` expects the lockfile in the checkout (eval.fetch copies the vendored one here)."""
        self._require_node()
        return self.checkout / "package-lock.json"

    @property
    def available(self) -> bool:
        if self.is_node:
            return self.checkout.is_dir() and self.deps_ready.is_file()
        return self.checkout.is_dir() and self.venv_ready.is_file()

    def link_deps(self, work: Path) -> None:
        """Give a working copy of this repo its node_modules: a symlink to the cached, read-only
        install, so nothing is copied and nothing in the cache can be edited through it."""
        self._require_node()
        if not self.deps_ready.is_file():
            raise FileNotFoundError(f"{self.name}: dependencies are not installed ({self.deps_dir}); run `python -m eval.fetch`")
        link = Path(work) / "node_modules"
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise FileExistsError(f"{link} is a real node_modules directory; refusing to replace it with the cached one")
        link.symlink_to(self.node_modules)

    def node_argv(self, targets: tuple[str, ...] | list[str] | None = None, flags: tuple[str, ...] = ()) -> list[str]:
        """The runner's command line, to run under `env(...)` so the bare name is the pinned binary.
        The default target is the repo's own test directory. Vitest gets --no-cache: before vitest 4
        it writes a results cache into node_modules/.vite, which is read-only here."""
        self._require_node()
        targets = [self.tests] if targets is None else list(targets)
        config = ["--config", self.config] if self.config else []
        if self.runner == "vitest":
            # Globs. Vitest 1.x refuses --exclude given twice, so several become one brace glob.
            glob = self.ignore[0] if len(self.ignore) == 1 else "{" + ",".join(self.ignore) + "}"
            exclude = ["--exclude", glob] if self.ignore else []
            return ["vitest", "run", "--no-cache", *config, *exclude, *flags, *targets]
        if self.ignore:
            # Jest's --testPathIgnorePatterns replaces the list in the repo's own config (tsyringe's keeps
            # /fixtures/ and /utils/ out), so leave the config alone and make each target a regular
            # expression that also refuses the ignored paths (each entry matched as a literal path).
            refused = "|".join(re.escape(p) for p in self.ignore)
            targets = [f"^(?!.*(?:{refused})).*{re.escape(t)}" for t in targets]
        return ["jest", *config, *flags, *targets]


def cache_root() -> Path:
    explicit = os.environ.get("RIPPLE_EVAL_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ripple-eval"


def _relative(name: str, key: str, value) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError(f"repos.json: {name}.{key} must be a relative path inside the repo, not {value!r}")
    return value


def _node_spec(name: str, data: dict, base: Path) -> RepoSpec:
    missing = [k for k in ("url", "sha", "manager", "runner", "tests", "lockfile") if k not in data]
    if missing:
        raise ValueError(f"repos.json: {name} is missing {', '.join(missing)}")
    for key in PYTHON_ONLY_KEYS:
        if key in data:
            raise ValueError(f"repos.json: {name}.{key} is a python setting, not valid for a node repo")
    if len(data["sha"]) != SHA or any(c not in "0123456789abcdef" for c in data["sha"]):
        raise ValueError(f"repos.json: {name}.sha must be a full 40-character commit SHA, not a tag or branch")
    if data["manager"] != "npm":
        raise ValueError(f"repos.json: {name}.manager must be npm (the only package manager supported), not {data['manager']!r}")
    if data["runner"] not in RUNNERS:
        raise ValueError(f"repos.json: {name}.runner must be {' or '.join(RUNNERS)}, not {data['runner']!r}")
    ignore = data.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(p, str) for p in ignore):
        raise ValueError(f"repos.json: {name}.ignore must be a list of strings")
    if data["runner"] == "vitest" and any("," in p for p in ignore):
        raise ValueError(f"repos.json: {name}.ignore entries may not contain a comma (vitest gets them as one {{a,b}} glob)")
    tests = _relative(name, "tests", data["tests"])
    config = _relative(name, "config", data["config"]) if "config" in data else ""
    # The lockfile is a file next to repos.json (eval/lockfiles/<name>/package-lock.json), never one
    # outside it: its bytes are hashed into the deps key and copied into the checkout.
    raw = data["lockfile"]
    lockfile = (base / raw) if isinstance(raw, str) and raw and not Path(raw).is_absolute() else None
    if lockfile is None or not lockfile.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"repos.json: {name}.lockfile must stay inside the directory of repos.json, not {raw!r}")
    if not lockfile.is_file():
        raise ValueError(f"repos.json: {name}.lockfile {raw} is not a file")
    return RepoSpec(
        name=name,
        url=data["url"],
        sha=data["sha"],
        describe=data.get("describe", ""),
        python="",
        pythonpath=(),
        tests=tests,
        deps=(),
        ignore=tuple(ignore),
        runtime="node",
        manager=data["manager"],
        runner=data["runner"],
        lockfile=lockfile.resolve(),
        config=config,
    )


def load_repos(path: Path = REPOS_FILE) -> dict[str, RepoSpec]:
    repos = {}
    for name, data in json.loads(path.read_text()).items():
        if not NAME.match(name):
            raise ValueError(f"repos.json: repo name {name!r} must match {NAME.pattern}")
        runtime = data.get("runtime", "python")
        if runtime not in RUNTIMES:
            raise ValueError(f'repos.json: {name}.runtime must be "python" or "node", not {runtime!r}')
        if runtime == "node":
            repos[name] = _node_spec(name, data, path.parent)
            continue
        for key in NODE_ONLY_KEYS:
            if key in data:
                raise ValueError(f"repos.json: {name}.{key} is a node setting, not valid for a python repo")
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
