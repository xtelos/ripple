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
from pathlib import Path

from . import __version__
from .indexer import build_graph, find_python_files
from .model import Graph

# Bump when the graph format or the resolution rules change, so old caches
# are ignored rather than served.
CACHE_FORMAT = f"{__version__}-2"


def default_cache_dir() -> Path:
    if os.environ.get("RIPPLE_CACHE_DIR"):
        return Path(os.environ["RIPPLE_CACHE_DIR"])
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "ripple"


def fingerprint(repo: Path) -> dict[str, list[int]]:
    """{relative path: [mtime_ns, size]} for every file the indexer would read."""
    result = {}
    for path in find_python_files(repo):
        stat = path.stat()
        result[path.relative_to(repo).as_posix()] = [stat.st_mtime_ns, stat.st_size]
    return result


def cache_file(repo: Path, cache_dir: Path) -> Path:
    key = hashlib.sha256(str(repo).encode()).hexdigest()[:16]
    return cache_dir / f"{repo.name}-{key}.json"


def load_graph(repo: str | Path, cache_dir: str | Path | None = None, files: dict | None = None) -> tuple[Graph, bool]:
    """Return (graph, came_from_cache). Rebuilds and saves when any file changed."""
    repo = Path(repo).resolve()
    path = cache_file(repo, Path(cache_dir) if cache_dir else default_cache_dir())
    files = files if files is not None else fingerprint(repo)
    try:
        saved = json.loads(path.read_text())
        if saved["format"] == CACHE_FORMAT and saved["files"] == files:
            return Graph.from_dict(saved["graph"]), True
    except (OSError, ValueError, KeyError, TypeError):
        pass  # missing or unreadable cache: rebuild
    graph = build_graph(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"format": CACHE_FORMAT, "files": files, "graph": graph.to_dict()}))
    except OSError:
        pass  # a read-only cache dir costs speed, not correctness
    return graph, False


class RepoIndex:
    """One repository's graph, held in memory for a long-running server."""

    def __init__(self, repo: str | Path, cache_dir: str | Path | None = None):
        self.repo = Path(repo).resolve()
        self.cache_dir = cache_dir
        self._files: dict | None = None
        self._graph: Graph | None = None

    def graph(self) -> Graph:
        files = fingerprint(self.repo)
        if self._graph is None or files != self._files:
            self._graph, _ = load_graph(self.repo, self.cache_dir, files)
            self._files = files
        return self._graph
