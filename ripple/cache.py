"""Keep the index on disk, keyed on file modification times.

Rebuilding the graph means parsing every file. Checking whether that is
needed only means listing the files and reading their mtime and size, which
is cheap, so every query does that check and reuses the saved graph when
nothing changed. An agent that edits a file and asks again gets a fresh
answer without having to remember to reindex.

The cache lives outside the repository (RIPPLE_CACHE_DIR, else
~/.cache/ripple) so ripple never writes into the code it is analyzing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import fields
from pathlib import Path

from . import __version__
from .indexer import build_graph, find_source_files
from .model import Graph
from .profile import PYTHON, LanguageProfile

# Bump when the graph format or the resolution rules change, so old caches
# are ignored rather than served.
CACHE_FORMAT = f"{__version__}-8"


def default_cache_dir() -> Path:
    if os.environ.get("RIPPLE_CACHE_DIR"):
        return Path(os.environ["RIPPLE_CACHE_DIR"])
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "ripple"


def fingerprint(repo: Path, profile: LanguageProfile = PYTHON) -> dict[str, list[int]]:
    """{relative path: [mtime_ns, size]} for every file the indexer would read, and for the
    profile's config files (tsconfig.json) that are there: an edit to one changes the index too. For TypeScript
    that includes every config the root one extends inside the repo, which the indexer reads as well."""
    result = {}
    names = [name for name in profile.config_files if (repo / name).is_file()]
    if profile.front_end == "typescript" and names:
        from .ts_modules import config_files  # no parser needed to read configs

        names = [*names, *(f for f in config_files(repo) if f not in names)]
    for path in [*find_source_files(repo, profile), *(repo / name for name in names)]:
        try:
            stat = path.stat()
        except OSError:
            continue  # deleted since the walk, or a link that stopped resolving
        result[path.relative_to(repo).as_posix()] = [stat.st_mtime_ns, stat.st_size]
    return result


def _describe(value: object) -> object:
    """A stable description of one profile field: sets sorted, callables by what they are."""
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    if callable(value):
        code = getattr(value, "__code__", None)
        body = ""
        if code is not None:  # a lambda or function: its bytecode, names and plain constants
            consts = [c for c in code.co_consts if isinstance(c, (str, int, type(None)))]
            body = repr((code.co_code.hex(), code.co_names, consts))
        return [getattr(value, "__module__", ""), getattr(value, "__qualname__", repr(value)), body]
    return value


def profile_key(profile: LanguageProfile) -> str:
    """What tells one profile's cache from another's.

    "" for PYTHON, so the Python cache is exactly the one that has always been written. Any other
    profile gets its name and a hash of all its fields: the name alone does not say what resolves
    differently, since two profiles can share one (a Python profile with another constructor name).
    """
    if profile == PYTHON:
        return ""
    described = {f.name: _describe(getattr(profile, f.name)) for f in fields(profile)}
    digest = hashlib.sha256(json.dumps(described, sort_keys=True, default=repr).encode()).hexdigest()[:12]
    return f"{re.sub(r'[^A-Za-z0-9_]+', '_', profile.name)}-{digest}"


def cache_file(repo: Path, cache_dir: Path, profile: LanguageProfile = PYTHON) -> Path:
    key = hashlib.sha256(str(repo).encode()).hexdigest()[:16]
    pkey = profile_key(profile)
    return cache_dir / (f"{repo.name}-{key}-{pkey}.json" if pkey else f"{repo.name}-{key}.json")


def load_graph(
    repo: str | Path,
    cache_dir: str | Path | None = None,
    files: dict | None = None,
    profile: LanguageProfile = PYTHON,
) -> tuple[Graph, bool]:
    """Return (graph, came_from_cache). Rebuilds and saves when any file changed."""
    repo = Path(repo).resolve()
    pkey = profile_key(profile)
    path = cache_file(repo, Path(cache_dir) if cache_dir else default_cache_dir(), profile)
    files = files if files is not None else fingerprint(repo, profile)
    try:
        saved = json.loads(path.read_text())
        # A record with no "profile" is a Python one: the only kind written before profiles existed.
        if saved["format"] == CACHE_FORMAT and saved["files"] == files and saved.get("profile", "") == pkey:
            return Graph.from_dict(saved["graph"]), True
    except (OSError, ValueError, KeyError, TypeError):
        pass  # missing or unreadable cache: rebuild
    graph = build_graph(repo, profile)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"format": CACHE_FORMAT, "files": files, "graph": graph.to_dict()}
        if pkey:  # left out for PYTHON, so its bytes do not change
            record["profile"] = pkey
        path.write_text(json.dumps(record))
    except OSError:
        pass  # a read-only cache dir costs speed, not correctness
    return graph, False


class RepoIndex:
    """One repository's graph, held in memory for a long-running server."""

    def __init__(self, repo: str | Path, cache_dir: str | Path | None = None, profile: LanguageProfile = PYTHON):
        self.repo = Path(repo).resolve()
        self.profile = profile
        self.cache_dir = cache_dir
        self._files: dict | None = None
        self._graph: Graph | None = None

    def graph(self) -> Graph:
        files = fingerprint(self.repo, self.profile)
        if self._graph is None or files != self._files:
            self._graph, _ = load_graph(self.repo, self.cache_dir, files, self.profile)
            self._files = files
        return self._graph
