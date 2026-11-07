"""Download the pinned repositories the real-repo eval tasks run over.

    python -m eval.fetch                  every repo in eval/repos.json
    python -m eval.fetch rich werkzeug    just those
    python -m eval.fetch --verify         also run each repo's own test suite and print the summary

For each repo this fetches exactly the pinned commit (a shallow `git fetch`
of the SHA, then a check that HEAD is that SHA), copies the tree without .git
into the cache directory (see eval/repos.py), and builds a virtualenv holding
the test dependencies, pinned to exact versions. The repository itself is never
installed or imported by this script: it is plain files until a test run, which
is the one thing --verify does, in the venv, from the cache copy.

Needs git and uv. Safe to re-run: a repo already in the cache is left alone.
"""

from __future__ import annotations

import argparse
import shutil
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


def verify(spec: RepoSpec) -> bool:
    """Run the repo's own suite from the cache copy, in a scratch copy so the cache stays pristine."""
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
            build_venv(specs[name])
            if args.verify:
                ok = verify(specs[name]) and ok
        except RuntimeError as err:
            print(f"  FAILED: {err}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
