"""Which file a TypeScript import names, and what each file is called.

A module is its path from the repo root without the extension (`src/cart/totals`), with an `index`
file standing for its directory (`src/cart`, and plain `index` at the root), because that is
what an import of the directory means. A symbol inside it follows a colon (`src/cart/totals:sum`).

An import specifier is turned into one of four answers (see ModuleMap.resolve):
  "repo"        a file the indexer reads; the value is its module id
  "package"     something outside the repo: a package, a node builtin, a file that is not source
  "missing"     a relative path that names no file at all
  "unresolved"  an alias of a config in the repo written in a file no config covers: it is not known to be a package
                (the value says why)

tsconfig.json is read for `baseUrl` and `paths` (the aliases `@app/*` and the like), and for `extends`
when it names another config in the repo by a relative path. An `extends` that names a package
(`@tsconfig/node18`) is not followed, so a `paths` that lives only there is not seen.

A config's `baseUrl` and `paths` are the aliases of the files it governs, and of no other. Every tsconfig.json
above an indexed file is read, and a file belongs to the nearest one whose `files`, `include` and `exclude`
cover it, as the compiler reads them (with no `include` and no `files`, every source file under the config's
directory; node_modules, bower_components, jspm_packages and `outDir` are left out when `exclude` is not
written). A file no config covers has no alias, so a bare import in it that needed one is never a repo file: it
names a package, except where a config above the file lists `references` and a config in that tree maps the
specifier, when it is "unresolved" (which project owns the file is then the open question). A config that covers
nothing of its own (`"files": []`, the solution-style root of a Vite or shadcn app) hands a file to the first
project it `references` that covers it; references are read to MAX_REFERENCE_DEPTH levels, each config once, a
directory path meaning its tsconfig.json, and are not inherited through `extends`. Only the root config's JSX
settings are read.

This module needs nothing outside the standard library. The parser is in ts_indexer.py.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
# `import "./x.js"` names x.ts: the compiler reads the extension the file will have after the build.
REWRITES = {".js": (".ts", ".tsx"), ".jsx": (".tsx", ".ts"), ".mjs": (".mts",), ".cjs": (".cts",)}
# What an import with no extension tries, then the same after a directory name's /index.
GUESSES = (".ts", ".tsx")
MAX_CONFIGS = 16  # tsconfig files read for one config, whatever it extends
MAX_REFERENCE_DEPTH = 4  # levels of `references` followed below a config (a config already read is not read again)
# What an include pattern may match (the compiler's supported extensions); `files` names a file outright.
SOURCE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
IMPLICIT_EXCLUDES = ("node_modules", "bower_components", "jspm_packages")


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
    """What the import resolver reads from one tsconfig.json.

    base_url is a repo-relative directory ("." is the repo root; it may start with ".." when the config
    puts it outside the repo, and then nothing resolves through it) or None when no config sets one.
    paths maps a pattern (`@app/*`) to its targets, each already relative to the repo root.
    files lists every config that was read (this one and what it extends inside the repo): an edit
    to any of them changes what an import means.
    The last three say which files the config governs, every path repo-relative: `covered` the files it names,
    `include` and `exclude` the patterns it lists (or the compiler's defaults). A config that is not there
    covers nothing.
    """

    base_url: str | None = None
    paths: dict[str, list[str]] = field(default_factory=dict)
    problem: str = ""  # why the config could not be read, "" when it was (or was not there)
    files: list[str] = field(default_factory=list)
    # why JSX is not compiled to React's calls, "" when it is (or nothing says otherwise): a jsxFactory, jsxFragmentFactory
    # or jsxImportSource that names something else
    jsx_runtime: str = ""
    covered: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    # the repo-relative path of this config, and the configs its `references` name that exist (each loaded the same way,
    # to MAX_REFERENCE_DEPTH levels, a config once however many name it). has_references is True when it lists any, read or not.
    path: str = ""
    references: tuple["TsConfig", ...] = ()
    has_references: bool = False

    def owner(self, rel: str) -> "TsConfig | None":
        """The config that governs the file: this one when it covers it, else the first project it references (and they
        reference, and so on) that does. A referenced project owns the files it covers, which is how a solution-style
        config (`"files": []` plus references) hands every file to the project that holds it."""
        if self.governs(rel):
            return self
        for project in self.references:
            found = project.owner(rel)
            if found is not None:
                return found
        return None

    def projects(self) -> list["TsConfig"]:
        """This config and every project it references, in the order they are consulted."""
        return [self, *(p for project in self.references for p in project.projects())]

    def governs(self, rel: str) -> bool:
        """Whether the config covers the file at this repo-relative path: it names it in `files`, or an `include`
        pattern matches it (and has a supported extension) and no `exclude` pattern does."""
        if rel in self.covered:
            return True
        if not rel.endswith(SOURCE_EXTENSIONS):
            return False
        parts = rel.split("/")
        if any(_matches(_segments(spec), parts, 0, 0, True, False) for spec in self.exclude):
            return False
        return any(_matches(_segments(spec, include=True), parts, 0, 0, False, True) for spec in self.include)


# ---- which files a config covers: the compiler's include and exclude patterns


def _segments(spec: str, include: bool = False) -> list[str]:
    """The path parts of a pattern. An include whose last part has no `.`, `*` or `?` names a directory, so it
    gets `/**/*` after it (the compiler's rule); one whose last part is `**` names no file at all."""
    parts = [] if spec in ("", ".") else spec.split("/")
    if include:
        if parts and parts[-1] == "**":
            return ["<nothing>"]  # no path part equals this, so nothing matches
        if not parts or not re.search(r"[.*?]", parts[-1]):
            parts = [*parts, "**", "*"]
    return parts


def _part_matches(pattern: str, part: str, include: bool) -> bool:
    if "*" not in pattern and "?" not in pattern:
        return pattern == part
    if include and part in IMPLICIT_EXCLUDES:
        return False
    head, body = "", pattern
    if include and body[0] == "*":  # a wildcard at the start of a part does not match a leading dot
        head, body = "(?:[^./][^/]*)?", body[1:]
    elif include and body[0] == "?":
        head, body = "[^./]", body[1:]
    regex = head + "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in body)
    return re.fullmatch(regex, part) is not None


def _matches(segments: list[str], parts: list[str], i: int, j: int, prefix: bool, include: bool) -> bool:
    """Whether the pattern parts from i match the path parts from j. `prefix` is an exclude, which also covers
    everything inside a directory it names; an include has to match the whole path."""
    if i == len(segments):
        return prefix or j == len(parts)
    if segments[i] == "**":
        k = j
        while True:
            if _matches(segments, parts, i + 1, k, prefix, include):
                return True
            # `**` stands for directories only. In an include it never takes a hidden one or node_modules; in an exclude it
            # takes any (the compiler's exclude matcher is `(/.+?)?`), so `**/*.stories.tsx` removes `.storybook/a.stories.tsx`
            if k >= len(parts) - 1 or (include and (parts[k].startswith(".") or parts[k] in IMPLICIT_EXCLUDES)):
                return False
            k += 1
    if j >= len(parts) or not _part_matches(segments[i], parts[j], include):
        return False
    return _matches(segments, parts, i + 1, j + 1, prefix, include)


def _slashes(path: str) -> str:
    """The path with `\\` as `/`, which is how the compiler reads every path a config writes."""
    return path.replace("\\", "/")


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
    times it is named (a cycle, or a list that names itself) and at most MAX_CONFIGS are read in all.
    The `files`, `include` and `exclude` lists and `outDir` come back as (the directory they were written in,
    the value): a pattern is relative to the config that wrote it, not to the one that extends it."""
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
            name = posixpath.normpath(posixpath.join(here, _slashes(parent)))
            if not name.endswith(".json"):
                name += ".json"
            if not name.startswith("..") and _inside(repo, name):
                options, problem_here = _read_options(repo, name, seen)
                merged.update(options)
                problem = problem or problem_here
    merged.pop("references", None)  # unlike the lists below, a project's references are not inherited through extends
    if isinstance(data.get("references"), list):
        merged["references"] = (here, data["references"])
    for key in ("files", "include", "exclude"):
        if isinstance(data.get(key), list):
            merged[key] = (here, data[key])
    options = data.get("compilerOptions")
    if isinstance(options, dict):
        base = options.get("baseUrl")
        if isinstance(base, str):
            merged["baseUrl"] = posixpath.normpath(posixpath.join(here, _slashes(base)))
        for name in ("jsxFactory", "jsxFragmentFactory", "jsxImportSource"):
            if isinstance(options.get(name), str):
                merged[name] = options[name]
        if isinstance(options.get("outDir"), str):
            merged["outDir"] = (here, _slashes(options["outDir"]))
        paths = options.get("paths")
        if isinstance(paths, dict):
            # a target is relative to baseUrl when there is one, else to the config that wrote paths
            merged["paths"] = {k: [_slashes(t) for t in v if isinstance(t, str)] for k, v in paths.items() if isinstance(v, list)}
            merged["pathsBase"] = here
    return merged, problem


def _specs(options: dict, key: str) -> tuple[str, ...] | None:
    """The patterns written under `key`, each as a repo-relative path, None when no config along the chain wrote any."""
    if key not in options:
        return None
    base, items = options[key]
    return tuple(posixpath.normpath(posixpath.join(base, _slashes(item))) for item in items if isinstance(item, str))


def _references(repo: Path, options: dict, loaded: set[str], depth: int) -> list[TsConfig]:
    """The configs the `references` of a config name that exist, loaded. A path is relative to the config that lists it;
    a `.json` path is the config itself and any other is a directory holding a tsconfig.json (the compiler's rule).
    `loaded` is shared by the whole tree, so a config named twice, or one that names an ancestor, is read once."""
    if "references" not in options or depth >= MAX_REFERENCE_DEPTH:
        return []
    base, items = options["references"]
    found = []
    for item in items:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path:
            continue
        name = posixpath.normpath(posixpath.join(base, _slashes(path)))
        if not name.endswith(".json"):
            name = posixpath.normpath(posixpath.join(name, "tsconfig.json"))
        if name.startswith("..") or name in loaded or not _inside(repo, name):
            continue
        loaded.add(name)
        found.append(load_tsconfig(repo, name, loaded, depth + 1))
    return found


def load_tsconfig(repo: Path, rel: str = "tsconfig.json", _loaded: set[str] | None = None, _depth: int = 0) -> TsConfig:
    """The tsconfig.json at the repo-relative path `rel` (the repo's root one by default) as a TsConfig, an empty
    one when there is none or it cannot be read. The projects it references are loaded with it (see `_references`)."""
    if not _inside(repo, rel):
        return TsConfig()
    here = posixpath.dirname(rel)
    seen: list[str] = []
    options, problem = _read_options(repo, rel, seen)
    loaded = {rel} if _loaded is None else _loaded
    projects = _references(repo, options, loaded, _depth)
    files = [f for f in seen if _inside(repo, f)]
    files += [f for project in projects for f in project.files if f not in files]
    base = options.get("baseUrl")
    # A baseUrl outside the repo stays: paths are relative to it, so they land outside the repo too and
    # name nothing ripple reads. Falling back to the config's own directory would name repo files that
    # the compiler never looks at.
    root = base if isinstance(base, str) else options.get("pathsBase", here)
    paths = {}
    for pattern, targets in options.get("paths", {}).items():
        paths[pattern] = [posixpath.normpath(posixpath.join(root, t)) for t in targets]
    runtime = ""
    for name, react in (("jsxFactory", "React.createElement"), ("jsxFragmentFactory", "React.Fragment"), ("jsxImportSource", "react")):
        if name in options and options[name] != react:
            runtime = runtime or f"{rel} sets {name} {options[name]}, not React's runtime, so when a component runs is not known"
    covered = _specs(options, "files")
    include = _specs(options, "include")
    exclude = _specs(options, "exclude")
    if include is None:  # `files` alone names the whole program; with neither, it is every file under the config
        include = () if covered is not None else (posixpath.normpath(posixpath.join(here, "**/*")),)
    if exclude is None:
        exclude = tuple(posixpath.normpath(posixpath.join(here, name)) for name in IMPLICIT_EXCLUDES)
        if "outDir" in options:
            out_base, out = options["outDir"]
            exclude += (posixpath.normpath(posixpath.join(out_base, out)),)
    return TsConfig(
        base_url=base,
        paths=paths,
        problem=problem,
        files=files,
        jsx_runtime=runtime,
        covered=covered or (),
        include=include,
        exclude=exclude,
        path=rel,
        references=tuple(projects),
        has_references=bool(options.get("references", ("", []))[1]),
    )


def find_configs(repo: Path, files: list[Path]) -> list[str]:
    """The repo-relative paths of the tsconfig.json files that could govern one of the files (the ones in a
    directory above or holding a file), the root one first."""
    dirs = {""}
    for path in files:
        rel = posixpath.dirname(path.relative_to(repo).as_posix())
        while rel and rel not in dirs:
            dirs.add(rel)
            rel = posixpath.dirname(rel)
    found = [posixpath.join(d, "tsconfig.json") for d in sorted(dirs)]
    return [rel for rel in found if _inside(repo, rel)]


def config_files(repo: Path, files: list[Path] | None = None) -> list[str]:
    """The configs the index of this repo depends on: every tsconfig.json above an indexed file and what each
    extends inside the repo. `files` are the indexed files when the caller has them."""
    if files is None:
        from .indexer import find_source_files
        from .profile import TYPESCRIPT

        files = find_source_files(repo, TYPESCRIPT)
    names: list[str] = []
    for rel in find_configs(repo, files):
        names += [f for f in load_tsconfig(repo, rel).files if f not in names]
    return names


class ModuleMap:
    """The modules of one repo, and the answer to "which file does this import name".

    Every file has a module id of its own. Two files that fold to one id (src/a.ts and src/a/index.ts, or
    a.ts and a.tsx) do not share it: the one an import of the plain name reaches (the compiler tries a.ts,
    then a.tsx, then the directory's index) keeps the id, and the other is given an explicit one (src/a/index,
    or its whole path with the extension) and is listed in `shadowed`.
    """

    def __init__(self, repo: Path, files: list[Path], config: TsConfig | None = None):
        # every tsconfig.json above a file, by its directory ("" for the root one); `config` is the root one
        self.configs: dict[str, TsConfig] = {}
        for rel in find_configs(repo, files):
            self.configs[posixpath.dirname(rel)] = config if config is not None and rel == "tsconfig.json" else load_tsconfig(repo, rel)
        self.config = config if config is not None else self.configs.get("", TsConfig())
        # the configs (the nested ones, and the projects they reference) that could not be read, by path
        self.problems = {p.path: p.problem for c in self.configs.values() for p in c.projects() if p.problem}
        self._governors: dict[str, TsConfig | None] = {}
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
        """("repo", module id), ("package", specifier), ("missing", specifier) or ("unresolved", why) for an import written in importer."""
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
        governor = self.governor(importer)
        if governor is None:
            why = self._unattributed(specifier, importer)
            return ("unresolved", why) if why else ("package", specifier)
        found = self._aliased(specifier, governor)
        return ("repo", self.modules[found]) if found else ("package", specifier)

    def governor(self, importer: str) -> TsConfig | None:
        """The config whose aliases the file has: the nearest tsconfig.json up its directory tree that covers it, or
        the project it references that does (see TsConfig.owner).

        None when no config does. A config that could not be read has no aliases and, with nothing read from it,
        the compiler's default coverage (everything under its directory), so a file below it is not handed the
        next config's aliases: which config is the file's is not known."""
        if importer not in self._governors:
            found: TsConfig | None = None
            here = posixpath.dirname(importer)
            while True:
                config = self.configs.get(here)
                found = config.owner(importer) if config is not None else None
                if found is not None:
                    break
                if not here:
                    break
                here = posixpath.dirname(here)
            self._governors[importer] = found
        return self._governors[importer]

    def _unattributed(self, specifier: str, importer: str) -> str:
        """Why a bare import in a file no config governs is not known to be a package, "" when it is not in doubt.

        It is in doubt when a config above the file lists `references` (so which project holds the file is a question
        the configs answer, and here they answer none) and a config in that tree gives the specifier an alias (a `paths`
        pattern that is not a bare `*`, or a repo file the `*` or `baseUrl` reach). Such an import is no more known to be
        a package than to be that alias, so it is left unresolved with the reason."""
        chain, here = [], posixpath.dirname(importer)
        while True:
            if here in self.configs:
                chain.append(self.configs[here])
            if not here:
                break
            here = posixpath.dirname(here)
        if not any(c.has_references for c in chain):
            return ""
        mapping = list(dict.fromkeys(p.path for c in chain for p in c.projects() if self._maps(specifier, p)))
        if not mapping:
            return ""
        return f"`{specifier}` is an alias in {', '.join(mapping)}, but no config covers {importer}, so whether this is that alias is not known"

    def _maps(self, specifier: str, config: TsConfig) -> bool:
        """Whether the config gives the specifier an alias: a `paths` entry it matches (a bare `*` aside), or a repo
        file its `paths` or `baseUrl` reach."""
        for pattern in config.paths:
            star = pattern.find("*")
            if star < 0:
                if pattern == specifier:
                    return True
            elif pattern != "*" and len(specifier) >= len(pattern) - 1 and specifier.startswith(pattern[:star]) and specifier.endswith(pattern[star + 1 :]):
                return True
        return self._aliased(specifier, config) is not None

    def _aliased(self, specifier: str, config: TsConfig) -> str | None:
        """The file a bare specifier names through the config's paths, then its baseUrl, else None."""
        best, captured = None, ""
        for pattern in config.paths:
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
            for target in config.paths[best]:
                found = self._file(posixpath.normpath(target.replace("*", captured, 1)))
                if found:
                    return found
        if config.base_url is not None:
            return self._file(posixpath.normpath(posixpath.join(config.base_url, specifier)))
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
