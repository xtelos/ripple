"""Download the pinned repositories the real-repo eval tasks run over.

    python -m eval.fetch                  every repo in eval/repos.json
    python -m eval.fetch rich werkzeug    just those
    python -m eval.fetch --verify         also run each repo's own test suite and print the summary

For each repo this fetches exactly the pinned commit (a shallow `git fetch`
of the SHA, then a check that HEAD is that SHA), copies the tree without .git
into the cache directory (see eval/repos.py), and builds what its suite runs in:
a virtualenv holding the test dependencies, pinned to exact versions, for a
Python repo; for a node repo the vendored package-lock.json (eval/lockfiles/) is
copied into the checkout and `npm ci --ignore-scripts` installs node_modules into
a read-only directory beside the checkout. The repository itself is never
installed or imported by this script, and no package's lifecycle script ever
runs: it is plain files until a test run, which is the one thing --verify does,
from a scratch copy of the cache copy.

Needs git, and uv (Python repos) or npm (node repos). Safe to re-run: a repo
already in the cache is left alone.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .repos import RepoSpec, cache_root, load_repos

# Left out of the copy: git history (the agent could read the real upstream
# change there) and the repo's own instructions to coding agents, which Claude
# Code would load from the working directory and which would differ per repo.
NOT_COPIED = (".git", "CLAUDE.md", "AGENTS.md", "GEMINI.md", ".claude", ".cursor", ".cursorrules")


def run(cmd: list[str], cwd: Path | None = None) -> str:
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed ({done.returncode}):\n{done.stderr.strip()[-800:]}")
    return done.stdout.strip()


def fetch_source(spec: RepoSpec) -> None:
    if spec.checkout.is_dir():
        print(f"  source: already at {spec.checkout}")
        return
    spec.checkout.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=spec.checkout.parent, prefix=".fetch-") as scratch:
        clone = Path(scratch) / "clone"
        clone.mkdir()
        run(["git", "init", "-q"], cwd=clone)
        run(["git", "fetch", "-q", "--depth", "1", spec.url, spec.sha], cwd=clone)
        run(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], cwd=clone)
        head = run(["git", "rev-parse", "HEAD"], cwd=clone)
        if head != spec.sha:
            raise RuntimeError(f"{spec.name}: fetched {head}, expected {spec.sha}")
        # symlinks=True keeps a link as a link: followed, a link out of the tree would copy
        # whatever it points at (a key in the fetching user's home) into the cache.
        shutil.copytree(clone, Path(scratch) / "tree", symlinks=True, ignore=shutil.ignore_patterns(*NOT_COPIED))
        (Path(scratch) / "tree").rename(spec.checkout)
    print(f"  source: {spec.sha[:12]} ({spec.describe}) -> {spec.checkout}")


def build_venv(spec: RepoSpec) -> None:
    if spec.venv_ready.is_file():
        print(f"  venv: already at {spec.venv}")
        return
    spec.venv.parent.mkdir(parents=True, exist_ok=True)
    if not spec.venv_python.exists():
        # --clear: an interrupted `uv venv` leaves a directory (pyvenv.cfg, no interpreter) that uv
        # otherwise refuses, and with no ready marker nothing finished lives there.
        run(["uv", "venv", "-q", "--clear", "--python", spec.python, str(spec.venv)])
    # A venv left by an install that failed has an interpreter but not its packages, so the
    # marker, not the interpreter, says it is finished. Installing again resumes it.
    run(["uv", "pip", "install", "-q", "--python", str(spec.venv_python), *spec.deps])
    spec.venv_ready.write_text("\n".join(spec.deps) + "\n")
    print(f"  venv: {len(spec.deps)} pinned packages -> {spec.venv}")


def place_lockfile(spec: RepoSpec) -> None:
    """Copy the vendored package-lock.json into the checkout, where `npm ci` and a person reading
    the repo expect it. Upstream's own lockfile, if the pin has one, must be the same bytes."""
    target = spec.lockfile_in_repo
    if target.is_file():
        if target.read_bytes() != spec.lockfile.read_bytes():
            raise RuntimeError(f"{spec.name}: the checkout's package-lock.json differs from eval/lockfiles/{spec.name}/")
        return
    shutil.copyfile(spec.lockfile, target)
    print(f"  lockfile: eval/lockfiles/{spec.name}/package-lock.json -> {target}")


def _chmod(path: Path, add: int = 0, drop: int = 0) -> None:
    if not path.is_symlink():  # a link's own mode is not settable, and chmod would follow it out of the tree
        path.chmod((stat.S_IMODE(path.stat().st_mode) | add) & ~drop)


def make_writable(root: Path) -> None:
    """Undo lock_down, so an unfinished install can be removed."""
    _chmod(root, add=0o700)
    for dirpath, dirnames, filenames in os.walk(root):  # topdown: a directory is opened before it is listed
        for name in dirnames:
            _chmod(Path(dirpath) / name, add=0o700)
        for name in filenames:
            _chmod(Path(dirpath) / name, add=0o200)


def lock_down(spec: RepoSpec) -> None:
    """Make the finished install read-only, then write the ready marker, then close the directory
    that holds it. An agent editing node_modules through its working copy's link cannot change
    what every other run reads. The marker comes last, so a half-locked directory is not ready."""
    for dirpath, dirnames, filenames in os.walk(spec.deps_dir):
        for name in (*dirnames, *filenames):
            _chmod(Path(dirpath) / name, drop=0o222)
    spec.deps_ready.write_text(f"{spec.manager} ci --ignore-scripts\n{spec.lockfile.name}\n")
    _chmod(spec.deps_ready, drop=0o222)
    _chmod(spec.deps_dir, drop=0o222)


def build_deps(spec: RepoSpec) -> None:
    if spec.deps_ready.is_file():
        print(f"  deps: already at {spec.deps_dir}")
        return
    spec.deps_dir.parent.mkdir(parents=True, exist_ok=True)
    if spec.deps_dir.exists():
        # With no ready marker nothing finished here: a partial install, maybe a partly locked one.
        make_writable(spec.deps_dir)
        shutil.rmtree(spec.deps_dir)
    spec.deps_dir.mkdir()
    # npm installs beside a package.json and its lockfile. These are copies, so the checkout never gains a node_modules.
    shutil.copyfile(spec.checkout / "package.json", spec.deps_dir / "package.json")
    shutil.copyfile(spec.lockfile, spec.deps_dir / "package-lock.json")
    run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=spec.deps_dir)
    lock_down(spec)
    count = len(list(spec.node_modules.glob("*/package.json"))) + len(list(spec.node_modules.glob("@*/*/package.json")))
    print(f"  deps: {count} top-level packages from the lockfile -> {spec.node_modules}")


# vitest prints "Tests  74 passed (74)", jest "Tests:       133 passed, 133 total" (on stderr)
SUMMARY = re.compile(r"^\s*Tests:?\s+\d.*$", re.MULTILINE)


def verify_node(spec: RepoSpec) -> bool:
    """The repo's own suite from a scratch copy whose node_modules is the cache's link, as every other run sees it."""
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch) / "repo"
        shutil.copytree(spec.checkout, work, symlinks=True, ignore=shutil.ignore_patterns("node_modules"))
        spec.link_deps(work)
        env = {**os.environ, **spec.env(work)}
        done = subprocess.run(spec.node_argv(), cwd=work, env=env, capture_output=True, text=True, timeout=1800)
    output = done.stdout + "\n" + done.stderr
    summary = SUMMARY.findall(output)
    tail = summary[-1].strip() if summary else ([line for line in output.splitlines() if line.strip()][-1:] or ["(no output)"])[0]
    print(f"  tests: exit {done.returncode}: {tail}")
    if done.returncode or not summary:
        print("\n".join(output.splitlines()[-25:]))
    return done.returncode == 0 and bool(summary)


def verify(spec: RepoSpec) -> bool:
    """Run the repo's own suite from the cache copy, in a scratch copy so the cache stays pristine."""
    if spec.is_node:
        return verify_node(spec)
    from .check import pytest_command

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch) / "repo"
        shutil.copytree(spec.checkout, work, symlinks=True)
        cmd = pytest_command(str(spec.venv_python), spec.tests, work, spec)
        done = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=1800)
    tail = [line for line in done.stdout.splitlines() if line.strip()][-1:] or ["(no output)"]
    print(f"  tests: exit {done.returncode}: {tail[0]}")
    if done.returncode:
        print("\n".join((done.stdout + done.stderr).splitlines()[-25:]))
    return done.returncode == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.fetch", description=__doc__.split("\n\n")[0])
    parser.add_argument("repos", nargs="*", help="repo names (default: all)")
    parser.add_argument("--verify", action="store_true", help="run each repo's own test suite after fetching")
    args = parser.parse_args(argv)

    specs = load_repos()
    unknown = [r for r in args.repos if r not in specs]
    if unknown:
        parser.error(f"unknown repo: {', '.join(unknown)} (known: {', '.join(specs)})")
    print(f"cache: {cache_root()}")
    ok = True
    for name in args.repos or list(specs):
        print(name)
        try:
            fetch_source(specs[name])
            if specs[name].is_node:
                place_lockfile(specs[name])
                build_deps(specs[name])
            else:
                build_venv(specs[name])
            if args.verify:
                ok = verify(specs[name]) and ok
        except RuntimeError as err:
            print(f"  FAILED: {err}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
