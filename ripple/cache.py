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
from .languages import (
    LanguageIndex,
    RepoError,
    RepoGraph,
    Scan,
    ambiguity_warning,
    merge_graphs,
    only_language_message,
    scan_repo,
    skipped_message,
)
from .model import Graph
from .profile import PYTHON, LanguageProfile
from .registry import default_language, front_end, is_missing_parser

# Bump when the graph format or the resolution rules change, so old caches
# are ignored rather than served.
CACHE_FORMAT = f"{__version__}-29"


def default_cache_dir() -> Path:
    if os.environ.get("RIPPLE_CACHE_DIR"):
        return Path(os.environ["RIPPLE_CACHE_DIR"])
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "ripple"


def fingerprint(repo: Path, profile: LanguageProfile = PYTHON, sources: list[Path] | None = None) -> dict[str, list[int]]:
    """{relative path: [mtime_ns, size]} for every file the indexer would read, and for the
    profile's config files (tsconfig.json, jsconfig.json) that are there: an edit to one changes the index too. For TypeScript and JavaScript
    that includes every tsconfig.json above an indexed file and every config each extends inside the repo,
    which the indexer reads as well (the front end's config_files, registry.py).
    sources are the profile's files when the caller has already walked the repo for them."""
    result = {}
    names = [name for name in profile.config_files if (repo / name).is_file()]
    sources = find_source_files(repo, profile) if sources is None else sources
    config_files = getattr(front_end(profile), "config_files", None)  # no parser needed to read configs
    if config_files is not None:
        names = [*names, *(f for f in config_files(repo, sources) if f not in names)]
    for path in [*sources, *(repo / name for name in names)]:
        try:
            stat = path.stat()
        except OSError:
            continue  # deleted since the walk, or a link that stopped resolving
        # relpath, not relative_to: a Rust path dependency outside the repo has its Cargo.toml here (`../lib/Cargo.toml`, or an absolute path's)
        try:
            key = Path(os.path.relpath(path, repo)).as_posix()
        except ValueError:  # Windows: a path on another drive has no relative form, so its absolute path is the key (it cannot equal a relative one)
            key = Path(os.path.abspath(path)).as_posix()
        result[key] = [stat.st_mtime_ns, stat.st_size]
    # files the answers depend on only by their being there (another language's sources): no size or time, so an edit
    # to one leaves this language's cache alone and adding or deleting one does not
    presence = getattr(front_end(profile), "presence_files", None)
    if presence is not None:
        for name in presence(repo, sources):
            result.setdefault(name, [0, 0])
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

    "" for the first language's profile (PYTHON), so the Python cache is exactly the one that has always
    been written. Any other profile gets its name and a hash of all its fields: the name alone does not
    say what resolves differently, since two profiles can share one (a Python profile with another
    constructor name).
    """
    if profile == default_language().profile:
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


def _index(scan: Scan, fingerprints: dict[str, dict], cache_dir: str | Path | None) -> RepoGraph:
    """Each language's graph (from its own cache entry, or built and saved there), merged.

    A language whose parser is missing is left out and said so, unless it is the repo's only one."""
    graphs, languages, missing = [], [], {}
    for profile in scan.profiles:
        language = LanguageIndex(profile.name, found=len(scan.files[profile.name]))
        languages.append(language)
        try:
            graph, language.cached = load_graph(scan.repo, cache_dir, fingerprints[profile.name], profile)
        except ImportError as error:
            if not is_missing_parser(error, profile):
                raise
            language.skipped = True
            missing[profile.name] = error
            continue
        language.files, language.parse_errors = language.found, list(graph.parse_errors)  # files read, unparsable too
        language.unread = list(graph.unread)
        graphs.append(graph)
    skipped = []
    for language in languages:
        if language.skipped:
            if not graphs:
                raise RepoError(only_language_message(scan.repo, language, missing[language.name]))
            skipped.append(skipped_message(language, missing[language.name], [x for x in languages if not x.skipped]))
    merged = merge_graphs(graphs)
    warnings = skipped + ([ambiguity_warning(merged.ambiguous_ids)] if merged.ambiguous_ids else [])
    return RepoGraph(merged, languages, skipped, warnings)


def load_repo(repo: str | Path, cache_dir: str | Path | None = None, lang: str = "auto") -> RepoGraph:
    """The graph of a repository in whichever languages it has (see languages.py), with what each one
    contributed. Raises RepoError for a repo that is missing, has nothing to read, or needs a missing parser."""
    scan = scan_repo(repo, lang)
    fingerprints = {p.name: fingerprint(scan.repo, p, scan.files[p.name]) for p in scan.profiles}
    return _index(scan, fingerprints, cache_dir)


class RepoIndex:
    """One repository's graph, held in memory for a long-running server.

    By default the languages are found from the files (lang "auto", or the name of one language). Given a
    profile, it indexes with that profile alone, whatever the files are.
    """

    def __init__(
        self,
        repo: str | Path,
        cache_dir: str | Path | None = None,
        profile: LanguageProfile | None = None,
        lang: str = "auto",
    ):
        self.repo = Path(repo).resolve()
        self.profile = profile
        self.lang = lang
        self.cache_dir = cache_dir
        self._files: dict | None = None
        self._loaded: RepoGraph | None = None

    def _scan(self) -> Scan:
        if self.profile is None:
            return scan_repo(self.repo, self.lang)
        return Scan(self.repo, [self.profile], {self.profile.name: find_source_files(self.repo, self.profile)}, [])

    def load(self) -> RepoGraph:
        """The graph and what went into it, rebuilt only for a language whose files changed."""
        scan = self._scan()
        files = {p.name: fingerprint(self.repo, p, scan.files[p.name]) for p in scan.profiles}
        if self._loaded is None or files != self._files:
            self._loaded = _index(scan, files, self.cache_dir)
            self._files = files
        return self._loaded

    def graph(self) -> Graph:
        return self.load().graph
