"""Which file a TypeScript import names, and what each file is called.

A module is its path from the repo root without the extension (`src/cart/totals`), with an `index`
file standing for its directory (`src/cart`, and plain `index` at the root), because that is
what an import of the directory means. A symbol inside it follows a colon (`src/cart/totals:sum`).

An import specifier is turned into one of three answers (see ModuleMap.resolve):
  "repo"     a file the indexer reads; the value is its module id
  "package"  something outside the repo: a package, a node builtin, a file that is not source
  "missing"  a relative path that names no file at all

tsconfig.json is read for `baseUrl` and `paths` (the aliases `@app/*` and the like), and for `extends`
when it names another config in the repo by a relative path. An `extends` that names a package
(`@tsconfig/node18`) is not followed, so a `paths` that lives only there is not seen. Only the
tsconfig.json at the repo root is read: a monorepo's per-package configs are not.

This module needs nothing outside the standard library. The parser is in ts_indexer.py.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
# `import "./x.js"` names x.ts: the compiler reads the extension the file will have after the build.
REWRITES = {".js": (".ts", ".tsx"), ".jsx": (".tsx", ".ts"), ".mjs": (".mts",), ".cjs": (".cts",)}
# What an import with no extension tries, then the same after a directory name's /index.
GUESSES = (".ts", ".tsx")
MAX_CONFIGS = 16  # tsconfig files read for one repo, whatever they extend


def _strip_suffix(rel: str) -> str:
    for suffix in SUFFIXES:
        if rel.endswith(suffix):
            return rel[: -len(suffix)]
    return rel


def _is_index(rel: str) -> bool:
    return _strip_suffix(rel).rpartition("/")[2] == "index"


def _suffix_rank(rel: str) -> int:
    return next((i for i, suffix in enumerate(SUFFIXES) if rel.endswith(suffix)), len(SUFFIXES))


def module_id(rel: str) -> str:
    """The module a source file (a repo-relative posix path) is: no extension, index folded into its directory.

    This is the id a file has unless another file folds to the same one (see ModuleMap)."""
    stem = _strip_suffix(rel)
    head, _, tail = stem.rpartition("/")
    if tail == "index" and head:
        return head
    return stem


def strip_jsonc(text: str) -> str:
    """The JSON in a tsconfig: comments and trailing commas removed, and nothing else touched.

    A string is copied as it is (a `//` inside "http://x" is not a comment), so this cannot
    turn valid JSON into something else.
    """
    text = text.lstrip("﻿")
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    stripped = "".join(out)
    # a comma followed (past white space) by a closing bracket is a trailing one
    result: list[str] = []
    i, n = 0, len(stripped)
    while i < n:
        c = stripped[i]
        if c == '"':
            j = i + 1
            while j < n and stripped[j] != '"':
                j += 2 if stripped[j] == "\\" else 1
            result.append(stripped[i : j + 1])
            i = j + 1
            continue
        if c == ",":
            j = i + 1
            while j < n and stripped[j] in " \t\r\n":
                j += 1
            if j < n and stripped[j] in "}]":
                i += 1
                continue
        result.append(c)
        i += 1
    return "".join(result)


@dataclass
class TsConfig:
    """What the import resolver reads from tsconfig.json.

    base_url is a repo-relative directory ("." is the repo root; it may start with ".." when the config
    puts it outside the repo, and then nothing resolves through it) or None when no config sets one.
    paths maps a pattern (`@app/*`) to its targets, each already relative to the repo root.
    files lists every config that was read (tsconfig.json and what it extends inside the repo): an edit
    to any of them changes what an import means.
    """

    base_url: str | None = None
    paths: dict[str, list[str]] = field(default_factory=dict)
    problem: str = ""  # why the config could not be read, "" when it was (or was not there)
    files: list[str] = field(default_factory=list)
    # why JSX is not compiled to React's calls, "" when it is (or nothing says otherwise): a jsxFactory, jsxFragmentFactory
    # or jsxImportSource that names something else
    jsx_runtime: str = ""


def _inside(repo: Path, rel: str) -> bool:
    """Whether the file the repo-relative path names is a file in the repo (a symlink may point out of it)."""
    try:
        path = repo / rel
        return path.is_file() and path.resolve().is_relative_to(repo.resolve())
    except OSError:
        return False


def _read_options(repo: Path, rel: str, seen: list[str]) -> tuple[dict, str]:
    """(compilerOptions with baseUrl and paths made repo-relative, a problem) of one config and its relative extends.

    seen holds the configs read so far and is shared by the whole chain, so a config is read once however many
    times it is named (a cycle, or a list that names itself) and at most MAX_CONFIGS are read in all."""
    if rel in seen or len(seen) >= MAX_CONFIGS:
        return {}, ""
    seen.append(rel)
    path = repo / rel
    try:
        data = json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeDecodeError) as error:
        return {}, f"{rel} could not be read: {error}"
    if not isinstance(data, dict):
        return {}, f"{rel} is not a JSON object"
    here = posixpath.dirname(rel)
    merged: dict = {}
    problem = ""
    parents = data.get("extends")
    for parent in parents if isinstance(parents, list) else [parents] if parents else []:
        # only a relative path into the repo; a package name is not followed
        if isinstance(parent, str) and parent.startswith("."):
            name = posixpath.normpath(posixpath.join(here, parent))
            if not name.endswith(".json"):
                name += ".json"
            if not name.startswith("..") and _inside(repo, name):
                options, problem_here = _read_options(repo, name, seen)
                merged.update(options)
                problem = problem or problem_here
    options = data.get("compilerOptions")
    if isinstance(options, dict):
        base = options.get("baseUrl")
        if isinstance(base, str):
            merged["baseUrl"] = posixpath.normpath(posixpath.join(here, base))
        for name in ("jsxFactory", "jsxFragmentFactory", "jsxImportSource"):
            if isinstance(options.get(name), str):
                merged[name] = options[name]
        paths = options.get("paths")
        if isinstance(paths, dict):
            # a target is relative to baseUrl when there is one, else to the config that wrote paths
            merged["paths"] = {k: [t for t in v if isinstance(t, str)] for k, v in paths.items() if isinstance(v, list)}
            merged["pathsBase"] = here
    return merged, problem


def load_tsconfig(repo: Path) -> TsConfig:
    """The repo's tsconfig.json as a TsConfig, an empty one when there is none or it cannot be read."""
    if not _inside(repo, "tsconfig.json"):
        return TsConfig()
    seen: list[str] = []
    options, problem = _read_options(repo, "tsconfig.json", seen)
    base = options.get("baseUrl")
    # A baseUrl outside the repo stays: paths are relative to it, so they land outside the repo too and
    # name nothing ripple reads. Falling back to the config's own directory would name repo files that
    # the compiler never looks at.
    root = base if isinstance(base, str) else options.get("pathsBase", "")
    paths = {}
    for pattern, targets in options.get("paths", {}).items():
        paths[pattern] = [posixpath.normpath(posixpath.join(root, t)) for t in targets]
    runtime = ""
    for name, react in (("jsxFactory", "React.createElement"), ("jsxFragmentFactory", "React.Fragment"), ("jsxImportSource", "react")):
        if name in options and options[name] != react:
            runtime = runtime or f"tsconfig.json sets {name} {options[name]}, not React's runtime, so when a component runs is not known"
    return TsConfig(base_url=base, paths=paths, problem=problem, files=[f for f in seen if _inside(repo, f)], jsx_runtime=runtime)


def config_files(repo: Path) -> list[str]:
    """The configs the index of this repo depends on: tsconfig.json and what it extends inside the repo."""
    return load_tsconfig(repo).files


class ModuleMap:
    """The modules of one repo, and the answer to "which file does this import name".

    Every file has a module id of its own. Two files that fold to one id (src/a.ts and src/a/index.ts, or
    a.ts and a.tsx) do not share it: the one an import of the plain name reaches (the compiler tries a.ts,
    then a.tsx, then the directory's index) keeps the id, and the other is given an explicit one (src/a/index,
    or its whole path with the extension) and is listed in `shadowed`.
    """

    def __init__(self, repo: Path, files: list[Path], config: TsConfig | None = None):
        self.config = config if config is not None else load_tsconfig(repo)
        self.modules: dict[str, str] = {}  # repo-relative path -> module id
        self.shadowed: list[str] = []  # the files that lost a clash over their id
        rels = [path.relative_to(repo).as_posix() for path in files]
        groups: dict[str, list[str]] = {}
        for rel in rels:
            groups.setdefault(module_id(rel), []).append(rel)
        used = set(groups)
        for folded, group in groups.items():
            group.sort(key=lambda rel: (_is_index(rel), _suffix_rank(rel), rel))
            self.modules[group[0]] = folded
            for rel in group[1:]:
                self.shadowed.append(rel)
                candidates = (_strip_suffix(rel), rel, *(f"{rel}#{n}" for n in range(2, len(rels) + 2)))
                chosen = next(c for c in candidates if c not in used)
                self.modules[rel] = chosen
                used.add(chosen)
        self.repo = repo

    def resolve(self, specifier: str, importer: str) -> tuple[str, str]:
        """("repo", module id), ("package", specifier) or ("missing", specifier) for an import written in importer."""
        if specifier.startswith("."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier))
            if base.startswith(".."):
                return "package", specifier
            found = self._file(base)
            if found:
                return "repo", self.modules[found]
            return ("package", specifier) if self._exists(base) else ("missing", specifier)
        if specifier.startswith("/"):
            return "package", specifier
        found = self._aliased(specifier)
        return ("repo", self.modules[found]) if found else ("package", specifier)

    def _aliased(self, specifier: str) -> str | None:
        """The file a bare specifier names through tsconfig paths, then baseUrl, else None."""
        best, captured = None, ""
        for pattern in self.config.paths:
            star = pattern.find("*")
            if star < 0:
                if pattern == specifier and (best is None or len(pattern) > len(best)):
                    best, captured = pattern, ""
                continue
            prefix, suffix = pattern[:star], pattern[star + 1 :]
            if len(specifier) >= len(prefix) + len(suffix) and specifier.startswith(prefix) and specifier.endswith(suffix):
                if best is None or len(prefix) > len(best.partition("*")[0]):
                    best, captured = pattern, specifier[len(prefix) : len(specifier) - len(suffix)]
        if best is not None:
            for target in self.config.paths[best]:
                found = self._file(posixpath.normpath(target.replace("*", captured, 1)))
                if found:
                    return found
        if self.config.base_url is not None:
            return self._file(posixpath.normpath(posixpath.join(self.config.base_url, specifier)))
        return None

    def _file(self, base: str) -> str | None:
        """The indexed file the path `base` (no extension, or one) names, in the order the compiler tries."""
        candidates = []
        if base.endswith(SUFFIXES):
            candidates.append(base)
        for js, replacements in REWRITES.items():
            if base.endswith(js):
                candidates += [base[: -len(js)] + ext for ext in replacements]
        candidates += [base + ext for ext in GUESSES]
        candidates += [f"{base}/index{ext}" for ext in GUESSES]
        return next((c for c in candidates if c in self.modules), None)

    def _exists(self, base: str) -> bool:
        try:
            return (self.repo / base).exists()
        except OSError:
            return False
