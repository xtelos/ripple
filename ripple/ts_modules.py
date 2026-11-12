"""Which file a TypeScript or JavaScript import names, and what each file is called.

A module is its path from the repo root without the extension (`src/cart/totals`), with an `index`
file standing for its directory (`src/cart`, and plain `index` at the root), because that is
what an import of the directory means. A symbol inside it follows a colon (`src/cart/totals:sum`).

An import specifier is turned into one of four answers (see ModuleMap.resolve):
  "repo"        a file the indexer reads; the value is its module id
  "package"     something outside the repo: a package, a node builtin, a file that is not source
  "missing"     a relative path that names no file at all
  "unresolved"  not known to be a package or a repo file (the value says why): an alias of a config in the repo written
                in a file no config covers, a file of the other language (a TypeScript file importing a .js file or a
                JavaScript file a .ts one: each language is its own graph, so the import is not followed), a directory
                of the repo (its package.json picks the file, which ripple does not read), a `#` import, or the name of a
                package.json of the repo
"package" and "missing" claim that nothing of the repo can be reached, which is made only when it is so.

Which file an import names is the compiler's answer, and ripple names a file only when the compiler, Node and the bundlers
agree on it. The compiler tries every TypeScript extension (.ts, .tsx, then the directory's index) and only then the
JavaScript ones (.js, .jsx), for a JavaScript importer too, and `./x.js` is the x.ts the build makes it when one is there.
Node and the bundlers run `./x.js` as written, and an extensionless `./x` is x.js for Node's require and the compiler but
x.mjs for Vite and Rollup (which also put .js before .ts). A specifier those disagree on is "unresolved", with the reason: a
guess would draw an edge to the wrong twin. The two languages share every id: x.js and x.ts are both `x`.

tsconfig.json is read for `baseUrl` and `paths` (the aliases `@app/*` and the like), and for `extends`
when it names another config in the repo by a relative path. An `extends` that names a package
(`@tsconfig/node18`) is not followed, so a `paths` that lives only there is not seen. A jsconfig.json is read the same
way (it is a tsconfig that allows JavaScript), for a TypeScript file as well as a JavaScript one: tsserver tries the
tsconfig.json and then the jsconfig.json of each directory, so a jsconfig.json is the project of every .js file the
tsconfig.json beside it does not hold. A .ts file is the build's, not the editor's: the nearest tsconfig.json that holds it
governs it, and only a file no tsconfig.json holds goes to the jsconfig.json above it that does. A config governs a .js file
only when it allows JavaScript (`allowJs` or `checkJs`, or it is a jsconfig.json). A wildcard include does not hold a file that a same-named file of a higher extension priority is
in (m.js beside m.ts, p.jsx beside p.js), as the compiler leaves it out; a file named in `files` is always held.

A config's `baseUrl` and `paths` are the aliases of the files it governs, and of no other. Every tsconfig.json
above an indexed file is read, and a file belongs to the nearest one whose `files`, `include` and `exclude`
cover it, as the compiler reads them (with no `include` and no `files`, every source file under the config's
directory; node_modules, bower_components, jspm_packages and `outDir` are left out when `exclude` is not
written). A file no config covers has no alias, so a bare import in it that needed one is never a repo file: it
names a package, except where a config above the file lists `references` and a config in that tree maps the
specifier, or a config whose include names the file keeps it out for a same-named twin (x.tsx beside x.ts: it is built
like its twin, with that config's aliases) and maps the specifier, when it is "unresolved" (which project owns the file is
then the open question). A `${configDir}` in a path of a config is the directory of the config that is compiled. A config that covers
nothing of its own (`"files": []`, the solution-style root of a Vite or shadcn app) hands a file to the first
project it `references` that covers it; references are read to MAX_REFERENCE_DEPTH levels, each config once, a
directory path meaning its tsconfig.json, and are not inherited through `extends`. The JSX settings read are the root
config's and those of the config that governs the file. `rootDirs` is not followed: a relative import that names no file
under a config that sets it is "unresolved", for the file may be in another of the roots.

Beyond the standard library this module needs the registry, to name the other language when an import reaches one of
its files, and the walk, to know which of them the other language's index reads. The parser is in ts_indexer.py.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import registry

SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs")  # the JAVASCRIPT profile's source_suffixes
# `import "./x.js"` names x.ts: the compiler reads the extension the file will have after the build.
REWRITES = {".js": (".ts", ".tsx"), ".jsx": (".tsx", ".ts"), ".mjs": (".mts",), ".cjs": (".cts",)}
# What an import with no extension tries, then the same after a directory name's /index. .mjs and .cjs are never tried
# by the compiler or by Node's require, and a .mjs is named in full.
GUESSES = (".ts", ".tsx")
JS_GUESSES = (".js", ".jsx")
DECLARATIONS = (".d.ts", ".d.mts", ".d.cts")
# The order Vite's resolve.extensions tries an extensionless import in (Rollup's node-resolve puts .mjs first too), and
# the one file Node's require adds: where they name another file than the compiler does, which one runs is not known.
BUNDLER_ORDER = (".mjs", ".js", ".mts", ".ts", ".jsx", ".tsx")
NODE_ORDER = (".js",)
# The extensions of a same-named file that keep a file out of a wildcard include (the compiler's extension priority:
# m.ts beside m.js holds m.ts only; a declaration file does not outrank a .js).
OUTRANKED_BY = {".tsx": (".ts",), ".js": (".ts", ".tsx"), ".jsx": (".ts", ".tsx", ".js"), ".mjs": (".mts",), ".cjs": (".cts",)}
MODULE_FORMATS = (".mjs", ".cjs")  # the suffixes a package ships a source under beside its .js (the dual-format layout)
CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")  # in the order tsserver tries them in a directory
MAX_CONFIGS = 16  # tsconfig files read for one config, whatever it extends
MAX_REFERENCE_DEPTH = 4  # levels of `references` followed below a config (a config already read is not read again)
# What an include pattern may match (the compiler's supported extensions); `files` names a file outright.
SOURCE_EXTENSIONS = SUFFIXES + JS_SUFFIXES
IMPLICIT_EXCLUDES = ("node_modules", "bower_components", "jspm_packages")
CONFIG_DIR = "${configDir}"  # TypeScript 5.5: in a path of a config, the directory of the config being compiled (the extending one)


def _strip_suffix(rel: str) -> str:
    for suffix in SOURCE_EXTENSIONS:
        if rel.endswith(suffix):
            return rel[: -len(suffix)]
    return rel


def _is_index(rel: str) -> bool:
    return _strip_suffix(rel).rpartition("/")[2] == "index"


def _suffix_rank(rel: str) -> int:
    """Which of two files that fold to one id keeps it: .ts, .tsx, .mts, .cts, then .js, .jsx, .mjs, .cjs. A map holds the
    files of one language, so this only orders the suffixes of that language."""
    return next((i for i, suffix in enumerate(SOURCE_EXTENSIONS) if rel.endswith(suffix)), len(SOURCE_EXTENSIONS))


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
    # whether JavaScript is part of the program: `allowJs` or `checkJs`, and always for a jsconfig.json. A .js file the
    # config does not allow is no file of its program, whatever `include` says, so the config's `paths` are not its aliases.
    allow_js: bool = False
    root_dirs: bool = False  # `rootDirs` is set: a relative import may name a file in another of the roots

    def owner(self, rel: str, exists: Callable[[str], bool] | None = None) -> "TsConfig | None":
        """The config that governs the file: this one when it covers it, else the first project it references (and they
        reference, and so on) that does. A referenced project owns the files it covers, which is how a solution-style
        config (`"files": []` plus references) hands every file to the project that holds it."""
        if self.governs(rel, exists):
            return self
        for project in self.references:
            found = project.owner(rel, exists)
            if found is not None:
                return found
        return None

    def projects(self) -> list["TsConfig"]:
        """This config and every project it references, in the order they are consulted."""
        return [self, *(p for project in self.references for p in project.projects())]

    def governs(self, rel: str, exists: Callable[[str], bool] | None = None) -> bool:
        """Whether the config covers the file at this repo-relative path: it names it in `files`, or an `include`
        pattern matches it (and has a supported extension) and no `exclude` pattern does, and, when `exists` says which
        files there are, no same-named file of a higher extension priority is in the include too (see OUTRANKED_BY)."""
        if rel.endswith(JS_SUFFIXES) and not self.allow_js:
            return False
        if rel in self.covered:
            return True
        if not self._included(rel):
            return False
        suffix = next((s for s in OUTRANKED_BY if rel.endswith(s)), "")
        return not (exists is not None and any(exists(sib) and self._included(sib) for sib in (rel[: -len(suffix)] + o for o in OUTRANKED_BY.get(suffix, ()))))

    def outranked(self, rel: str, exists: Callable[[str], bool]) -> bool:
        """Whether the config's include holds the file and only a same-named file of higher extension priority (x.ts beside
        x.tsx, p.js beside p.jsx) keeps it out of the program: the file has no project, yet it is built like its twin."""
        if rel in self.covered or (rel.endswith(JS_SUFFIXES) and not self.allow_js) or not self._included(rel):
            return False
        return not self.governs(rel, exists)

    def _included(self, rel: str) -> bool:
        """Whether the include and exclude patterns hold the file, whatever else is beside it."""
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


def _anchored(top: str, value: str) -> str | None:
    """The repo-relative path of a value written under `${configDir}` (the directory `top` of the config being compiled),
    None when it is not written that way."""
    if not value.startswith(CONFIG_DIR):
        return None
    return posixpath.normpath(posixpath.join(top, value[len(CONFIG_DIR) :].lstrip("/")))


def _read_options(repo: Path, rel: str, seen: list[str], top: str | None = None) -> tuple[dict, str]:
    """(compilerOptions with baseUrl and paths made repo-relative, a problem) of one config and its relative extends.

    seen holds the configs read so far and is shared by the whole chain, so a config is read once however many
    times it is named (a cycle, or a list that names itself) and at most MAX_CONFIGS are read in all.
    The `files`, `include` and `exclude` lists and `outDir` come back as (the directory they were written in,
    the value): a pattern is relative to the config that wrote it, not to the one that extends it. `top` is the
    directory of the config that started the chain, which a `${configDir}` in any of them stands for."""
    if rel in seen or len(seen) >= MAX_CONFIGS:
        return {}, ""
    seen.append(rel)
    top = posixpath.dirname(rel) if top is None else top
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
                options, problem_here = _read_options(repo, name, seen, top)
                merged.update(options)
                problem = problem or problem_here
    merged.pop("references", None)  # unlike the lists below, a project's references are not inherited through extends
    if isinstance(data.get("references"), list):
        merged["references"] = (here, data["references"])
    for key in ("files", "include", "exclude"):
        if isinstance(data.get(key), list):
            # a `${configDir}` pattern is made relative to this config, so it is joined to it like the others
            merged[key] = (here, [posixpath.relpath(_anchored(top, i), here or ".") if isinstance(i, str) and i.startswith(CONFIG_DIR) else i for i in data[key]])
    options = data.get("compilerOptions")
    if isinstance(options, dict):
        base = options.get("baseUrl")
        if isinstance(base, str):
            merged["baseUrl"] = _anchored(top, base) or posixpath.normpath(posixpath.join(here, _slashes(base)))
        for name in ("jsxFactory", "jsxFragmentFactory", "jsxImportSource"):
            if isinstance(options.get(name), str):
                merged[name] = options[name]
        for name in ("allowJs", "checkJs"):
            if isinstance(options.get(name), bool):
                merged[name] = options[name]
        if isinstance(options.get("outDir"), str):
            merged["outDir"] = ("", _anchored(top, options["outDir"])) if options["outDir"].startswith(CONFIG_DIR) else (here, _slashes(options["outDir"]))
        if isinstance(options.get("rootDir"), str):
            merged["rootDir"] = _anchored(top, options["rootDir"]) or posixpath.normpath(posixpath.join(here, _slashes(options["rootDir"])))
        if isinstance(options.get("rootDirs"), list):
            merged["rootDirs"] = bool(options["rootDirs"])
        paths = options.get("paths")
        if isinstance(paths, dict):
            # a target is relative to baseUrl when there is one, else to the config that wrote paths
            merged["paths"] = {k: [_slashes(t) for t in v if isinstance(t, str)] for k, v in paths.items() if isinstance(v, list)}  # a ${configDir} target is read in load_tsconfig
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
        paths[pattern] = [_anchored(here, t) or posixpath.normpath(posixpath.join(root, t)) for t in targets]
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
        # the compiler's own default: allowJs when it is written, else checkJs
        allow_js=posixpath.basename(rel) == "jsconfig.json" or bool(options["allowJs"] if "allowJs" in options else options.get("checkJs")),
        root_dirs=bool(options.get("rootDirs")),
    )


def find_configs(repo: Path, files: list[Path]) -> list[str]:
    """The repo-relative paths of the configs that could govern one of the files (the ones in a directory above
    or holding a file), the root one first, and in a directory that has both its tsconfig.json before its jsconfig.json
    (the order tsserver tries them in, for a TypeScript file as for a JavaScript one)."""
    dirs = {""}
    for path in files:
        rel = posixpath.dirname(path.relative_to(repo).as_posix())
        while rel and rel not in dirs:
            dirs.add(rel)
            rel = posixpath.dirname(rel)
    found = []
    for directory in sorted(dirs):
        found += [posixpath.join(directory, name) for name in CONFIG_NAMES if _inside(repo, posixpath.join(directory, name))]
    return found


def _other_sources(repo: Path, files: list[Path]) -> list[str]:
    """The source files of the other language this front end reads (the .js files beside a TypeScript repo, the .ts files
    beside a JavaScript one) that the walk finds, repo-relative: the files an import of this language may name and its own
    index does not read. Only their being there matters to this language's answers, never what is in them."""
    from .indexer import find_source_files

    if not files:
        return []
    own = registry.language_of(files[0].name)
    found = [
        path.relative_to(repo).as_posix()
        for language in registry.LANGUAGES.values()
        if language.front_end == own.front_end and language.profile.name != own.profile.name
        for path in find_source_files(repo, language.profile)
    ]
    return sorted(found)


def config_files(repo: Path, files: list[Path] | None = None) -> list[str]:
    """The files besides the sources that the index of this repo depends on, whatever is in them: every config above an
    indexed file and what each extends inside the repo. `files` are the indexed files when the caller has them."""
    if files is None:
        from .indexer import find_source_files
        from .profile import TYPESCRIPT

        files = find_source_files(repo, TYPESCRIPT)
    names: list[str] = []
    for rel in find_configs(repo, files):
        names += [f for f in load_tsconfig(repo, rel).files if f not in names]
    # a package.json decides whether a directory import names its index file, and its `name` marks a bare import as one of this repo's
    names += [f for f in _package_files(repo, [path.relative_to(repo).as_posix() for path in files] + _other_sources(repo, files)) if f not in names]
    return names


def _package_files(repo: Path, rels: list[str]) -> list[str]:
    """The repo-relative paths of the package.json files in a directory holding one of the files or above it, shallowest first."""
    dirs = {""}
    for rel in rels:
        directory = posixpath.dirname(rel)
        while directory and directory not in dirs:
            dirs.add(directory)
            directory = posixpath.dirname(directory)
    return [posixpath.join(d, "package.json") for d in sorted(dirs) if _inside(repo, posixpath.join(d, "package.json"))]


def output_dirs(repo: Path, directory: str, filenames: list[str]) -> list[str]:
    """The repo-relative directories a tsconfig.json or jsconfig.json in `directory` (a repo-relative path, "" for the root)
    names as the compiler's `outDir`, through the configs it extends and the ones it references inside the repo (a
    library's tsconfig.json is often `"files": []` plus a reference to the tsconfig.build.json that holds the outDir).
    What is emitted there is built output (the compiler leaves it out of the program unless `exclude` says otherwise),
    never source: reading it would index every function twice. Only the TypeScript and JavaScript walks skip them (see
    indexer.walk_languages). A directory is not an output directory when it is, or is above, the directory of the
    config that names it, its `rootDir`, or a root of its `include` or `files` (that is in-place emit, or the sources
    themselves), nor when it holds a TypeScript source file (a .ts that is no declaration: a compiler writes none).
    Called by the walk, which lists what it skips."""
    found: list[str] = []
    seen: set[str] = set()
    for name in CONFIG_NAMES:
        if name in filenames:
            for out in _outputs_of(repo, posixpath.join(directory, name), seen, 0):
                if out not in found:
                    found.append(out)
    return found


def _outputs_of(repo: Path, rel: str, seen: set[str], depth: int) -> list[str]:
    """The output directories of the config at `rel` and of the projects it references (see output_dirs)."""
    if rel in seen or depth > MAX_REFERENCE_DEPTH or not _inside(repo, rel):
        return []
    seen.add(rel)
    here = posixpath.dirname(rel)
    options, _ = _read_options(repo, rel, [])
    found: list[str] = []
    if "outDir" in options:
        base, out = options["outDir"]
        out = posixpath.normpath(posixpath.join(base, out))
        if _is_output(repo, out, here, options):
            found.append(out)
    for item in options.get("references", ("", []))[1]:
        path = item.get("path") if isinstance(item, dict) else None
        if isinstance(path, str) and path:
            name = posixpath.normpath(posixpath.join(options["references"][0], _slashes(path)))
            if not name.endswith(".json"):
                name = posixpath.normpath(posixpath.join(name, "tsconfig.json"))
            if not name.startswith(".."):
                found += _outputs_of(repo, name, seen, depth + 1)
    return found


def _is_output(repo: Path, out: str, here: str, options: dict) -> bool:
    """Whether the directory `out` that the config in `here` names as outDir is build output (see output_dirs)."""
    if out in ("", ".") or out.startswith("..") or not _inside_dir(repo, out) or here == out or here.startswith(out + "/"):
        return False
    roots = [options["rootDir"]] if "rootDir" in options else []
    for key in ("include", "files"):
        for spec in _specs(options, key) or ():
            parts = spec.split("/")
            fixed = parts[: next((i for i, part in enumerate(parts) if re.search(r"[*?]", part)), len(parts))]  # the part before the first wildcard
            roots.append("/".join(fixed[:-1] if key == "files" or (fixed and "." in fixed[-1]) else fixed) or ".")
    if any(root == out or root.startswith(out + "/") for root in roots):
        return False
    return not _holds_typescript_source(repo / out)


def _holds_typescript_source(directory: Path) -> bool:
    """Whether a .ts, .tsx, .mts or .cts file that is no declaration is in the directory or below it."""
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d not in IMPLICIT_EXCLUDES]
        if any(f.endswith(SUFFIXES) and not f.endswith(DECLARATIONS) for f in filenames):
            return True
    return False


def _inside_dir(repo: Path, rel: str) -> bool:
    try:
        return (repo / rel).resolve().is_relative_to(repo.resolve())
    except OSError:
        return False


def presence_files(repo: Path, files: list[Path]) -> list[str]:
    """The files the index of this repo depends on only by their being there (cache.fingerprint records no size or time
    for them): the other language's source files, which decide whether an import is unresolved or names nothing, and
    which same-named file keeps a file out of a config's include."""
    return _other_sources(repo, files)


def _candidates(base: str, js: bool, directory: bool = False) -> list[str]:
    """The files an import of the path `base` (no extension, or one) may name for the TypeScript (js False) or the
    JavaScript pass of the compiler, in the order tried. `base` is `.` when `..` is written one level below the root, and
    that is the root's index file, not a file called `.ts`. A `directory` is written with a trailing slash (or is `.` or
    `..`): only its index file is a candidate."""
    pre = "" if base == "." else base + "/"
    plain = [] if base == "." or directory else [base]
    if js:
        written = plain if base.endswith(JS_SUFFIXES) else []
        return written + [b + ext for b in plain for ext in JS_GUESSES] + [f"{pre}index{ext}" for ext in JS_GUESSES]
    written = list(plain) if base.endswith(SUFFIXES) else []
    for ext, replacements in REWRITES.items():
        if base.endswith(ext) and not directory:
            written += [base[: -len(ext)] + other for other in replacements]
    return written + [b + ext for b in plain for ext in GUESSES] + [f"{pre}index{ext}" for ext in GUESSES]


class ModuleMap:
    """The modules of one repo, and the answer to "which file does this import name".

    Every file has a module id of its own. Two files that fold to one id (src/a.ts and src/a/index.ts, or
    a.ts and a.tsx) do not share it: the one an import of the plain name reaches (the compiler tries a.ts,
    then a.tsx, then the directory's index) keeps the id, and the other is given an explicit one (src/a/index,
    or its whole path with the extension) and is listed in `shadowed`. A JavaScript package that ships one source
    twice (x.js and x.mjs, and x.cjs: the dual-format layout) is the same: x.js keeps `x`, and the others are
    also listed in `dual_format`, which is not a failure to read them.

    A map holds the files of one language, and resolve answers for an importer of it. A file of the other language
    that an import names is not in the map, and is told from a package (see `_pick` and `_has`).
    """

    def __init__(self, repo: Path, files: list[Path], config: TsConfig | None = None):
        # every config above a file by its directory ("" for the root one): the tsconfig.json, or the jsconfig.json
        # when there is no tsconfig.json; `config` is the root tsconfig.json. The jsconfig.json beside a tsconfig.json
        # is in `alongside`: it is the project of the files the tsconfig.json does not hold.
        self.configs: dict[str, TsConfig] = {}
        self.alongside: dict[str, TsConfig] = {}
        for rel in find_configs(repo, files):
            loaded = config if config is not None and rel == "tsconfig.json" else load_tsconfig(repo, rel)
            (self.alongside if posixpath.dirname(rel) in self.configs else self.configs)[posixpath.dirname(rel)] = loaded
        self.config = config if config is not None else self.configs.get("", TsConfig())
        # the configs (the nested ones, and the projects they reference) that could not be read, by path
        self.problems = {p.path: p.problem for c in [*self.configs.values(), *self.alongside.values()] for p in c.projects() if p.problem}
        self._governors: dict[str, TsConfig | None] = {}
        self._others: set[str] | None = None  # the other language's files the walk finds (see _has)
        self._elsewhere: dict[tuple, str] = {}  # (specifier, importer) -> _aliased_elsewhere's answer
        self._names: dict[str, str] | None = None  # package name -> its package.json (see _package_names)
        self._directories: dict[str, bool] = {}  # repo-relative path -> whether it is a directory of the repo (see _directory)
        self.files = files
        self.modules: dict[str, str] = {}  # repo-relative path -> module id
        self.shadowed: list[str] = []  # the files that lost a clash over their id
        self.dual_format: list[str] = []  # the shadowed JavaScript files that are one source in another format of the winner's
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
                if rel.endswith(MODULE_FORMATS) and group[0].endswith(JS_SUFFIXES) and _strip_suffix(rel) == _strip_suffix(group[0]):
                    self.dual_format.append(rel)
                candidates = (_strip_suffix(rel), rel, *(f"{rel}#{n}" for n in range(2, len(rels) + 2)))
                chosen = next(c for c in candidates if c not in used)
                self.modules[rel] = chosen
                used.add(chosen)
        self.repo = repo

    def resolve(self, specifier: str, importer: str) -> tuple[str, str]:
        """("repo", module id), ("package", specifier), ("missing", specifier) or ("unresolved", why) for an import written in importer.

        "package" and "missing" are claims that the import cannot reach code of the repo, so they are made only when
        nothing the repo holds could be named: a directory, a package.json name or `imports` entry, or an alias of a
        config that does not govern the importer is left unresolved, because the file it names is decided by something
        ripple does not read or does not know applies."""
        if specifier.startswith("."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier))
            if base.startswith(".."):
                return "package", specifier
            # a trailing slash, or `.` or `..` as the last part, names a directory and never a file
            directory = specifier.endswith("/") or specifier.rpartition("/")[2] in (".", "..")
            found, why = self._pick([base], importer, specifier, directory)
            if why:
                return "unresolved", why
            if found:
                return self._reached(found, specifier, importer)
            exists = self._exists(base)
            if exists and base.endswith(DECLARATIONS):
                return "unresolved", f"`{specifier}` names {base}, a declaration file: it holds types and no code, so what a name from it runs is not known"
            governor = self.governor(importer)
            if not exists and governor is not None and governor.root_dirs:
                return "unresolved", f"{governor.path} sets rootDirs, so `{specifier}` may name a file in another of the roots, which ripple does not follow"
            return ("package", specifier) if exists else ("missing", specifier)
        if specifier.startswith("/"):
            return "package", specifier
        if specifier.startswith("#"):
            return "unresolved", f"`{specifier}` is a subpath import: the `imports` of the nearest package.json maps it, and ripple does not read package.json"
        governor = self.governor(importer)
        if governor is not None:
            found, why = self._pick(self._places(specifier, governor), importer, specifier)
            if why:
                return "unresolved", why
            if found:
                return self._reached(found, specifier, importer)
        why = self._aliased_elsewhere(specifier, importer, governor) or self._own_package(specifier)
        return ("unresolved", why) if why else ("package", specifier)

    def _reached(self, found: str, specifier: str, importer: str) -> tuple[str, str]:
        """The answer for a specifier that reached the file `found` (see `_pick`): its module, or, for a file of the other
        language, why it is not followed. It is no package and no repo file ripple reads in this pass, and counting its
        calls as outside the repo would say it is code ripple cannot see when it is code ripple reads in the other pass."""
        if found in self.modules:
            return "repo", self.modules[found]
        here, there = (registry.label(registry.language_of(path).profile.name) for path in (importer, found))
        return "unresolved", f"`{specifier}` names {found}, a {there} file, whose calls ripple does not follow from {here}"

    def jsx_runtime(self, importer: str) -> str:
        """Why JSX in this file is not known to compile to React's calls, "" when nothing says otherwise: what the config
        that governs the file sets, and what the root config does (a file no config holds may still be built with it)."""
        governor = self.governor(importer)
        return (governor.jsx_runtime if governor is not None else "") or self.config.jsx_runtime

    def governor(self, importer: str) -> TsConfig | None:
        """The config whose aliases the file has: the nearest config up its directory tree that holds it, or the project
        it references that does (see TsConfig.owner). In a directory with both, the tsconfig.json is tried before the
        jsconfig.json beside it, and a JavaScript file the tsconfig.json does not hold is the jsconfig.json's, as tsserver
        assigns it.

        A TypeScript file is the build's: the nearest tsconfig.json that holds it, however near a jsconfig.json is (tsc
        compiles it with that tsconfig.json's paths, where tsserver would open it in the jsconfig.json). Only a file no
        tsconfig.json holds goes to the jsconfig.json above it that does, which is the project tsserver gives it.

        None when no config does. A config that could not be read has no aliases and, with nothing read from it,
        the compiler's default coverage (everything under its directory), so a file below it is not handed the
        next config's aliases: which config is the file's is not known."""
        if importer not in self._governors:
            if importer.endswith(SUFFIXES):
                found = self._climb(importer, ("tsconfig.json",)) or self._climb(importer, ("jsconfig.json",))
            else:
                found = self._climb(importer, CONFIG_NAMES)
            self._governors[importer] = found
        return self._governors[importer]

    def _climb(self, importer: str, names: tuple[str, ...]) -> TsConfig | None:
        """The nearest config named in `names` that holds the file, trying each directory from the file's up."""
        here = posixpath.dirname(importer)
        while True:
            for config in (self.configs.get(here), self.alongside.get(here)):
                if config is not None and posixpath.basename(config.path) in names:
                    found = config.owner(importer, self._has)
                    if found is not None:
                        return found
            if not here:
                return None
            here = posixpath.dirname(here)

    def _aliased_elsewhere(self, specifier: str, importer: str, governor: TsConfig | None) -> str:
        """Why a bare import that the governing config (or none) does not give a repo file is not known to be a package,
        "" when it is not in doubt. Two things leave a file's aliases unknown:

        - a config whose include names the file keeps it out for a same-named file of higher priority (x.tsx beside x.ts):
          the file is in no project of the compiler, but it is built like its twin, with that config's aliases, and the
          config maps the specifier;
        - a config above the file lists `references` (so which project holds the file is a question the configs answer,
          and here they answer none) and a config in that tree maps the specifier.

        Such an import is no more a package than the file the alias names, so it is left unresolved with the reason. A file
        that no include names (an app the root config leaves out) has no aliases, and its bare import is a package."""
        if governor is not None:
            return ""
        key = (specifier, importer)
        if key not in self._elsewhere:
            every = [p for c in [*self.configs.values(), *self.alongside.values()] for p in c.projects()]
            twin = list(dict.fromkeys(p.path for p in every if p.outranked(importer, self._has) and self._maps(specifier, p, importer)))
            if twin:
                self._elsewhere[key] = (
                    f"`{specifier}` is an alias in {', '.join(twin)}, whose include holds {importer} but for a same-named file of higher "
                    "priority, so which project builds it is not known"
                )
            else:
                self._elsewhere[key] = self._unattributed(specifier, importer)
        return self._elsewhere[key]

    def _unattributed(self, specifier: str, importer: str) -> str:
        """Why a bare import in a file no config governs is not known to be a package, "" when it is not in doubt.

        It is in doubt when a config above the file lists `references` and a config in that tree gives the specifier an
        alias (a `paths` pattern that is not a bare `*`, or a repo file the `*` or `baseUrl` reach)."""
        chain, here = [], posixpath.dirname(importer)
        while True:
            chain += [c for c in (self.configs.get(here), self.alongside.get(here)) if c is not None]
            if not here:
                break
            here = posixpath.dirname(here)
        if not any(c.has_references for c in chain):
            return ""
        mapping = list(dict.fromkeys(p.path for c in chain for p in c.projects() if self._maps(specifier, p, importer)))
        if not mapping:
            return ""
        return f"`{specifier}` is an alias in {', '.join(mapping)}, but no config covers {importer}, so whether this is that alias is not known"

    def _own_package(self, specifier: str) -> str:
        """Why a bare import is not known to be a package from outside the repo, "" when it is: its package name is the `name`
        of a package.json of the repo. A package imports itself by its name (its `exports` map the subpaths), and a
        workspace package is imported by its name from the others (linked into node_modules): both reach repo code through
        a package.json that ripple does not read beyond its `name`."""
        parts = specifier.split("/")
        name = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
        where = self._package_names().get(name)
        if where is None:
            return ""
        return f"`{specifier}` names the package {name}, which {where} is in this repo, and its entry is chosen by that package.json, which ripple does not follow"

    def _package_names(self) -> dict[str, str]:
        """{package name: the package.json of the repo that holds it}, from the `name` of the package.json files above the sources
        of either language (nothing else of one is read)."""
        if self._names is None:
            self._names = {}
            for rel in _package_files(self.repo, [*self.modules, *self._has_others()]):
                try:
                    data = json.loads((self.repo / rel).read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
                if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"]:
                    self._names.setdefault(data["name"], rel)
        return self._names

    def _maps(self, specifier: str, config: TsConfig, importer: str) -> bool:
        """Whether the config gives the specifier an alias: a `paths` entry it matches (a bare `*` aside), or a repo
        file or directory its `paths` or `baseUrl` reach."""
        for key in config.paths:
            star = key.find("*")
            if star < 0:
                if key == specifier:
                    return True
            elif key != "*" and len(specifier) >= len(key) - 1 and specifier.startswith(key[:star]) and specifier.endswith(key[star + 1 :]):
                return True
        found, why = self._pick(self._places(specifier, config), importer, specifier)
        return found is not None or bool(why)

    def _places(self, specifier: str, config: TsConfig) -> list[str]:
        """The repo paths (no extension, or one) a bare specifier may name through the config, in the order the compiler
        looks: each target of the longest `paths` pattern that matches, then the specifier under `baseUrl`."""
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
        places = [posixpath.normpath(target.replace("*", captured, 1)) for target in config.paths[best]] if best is not None else []
        if config.base_url is not None:
            places.append(posixpath.normpath(posixpath.join(config.base_url, specifier)))
        return places

    def _pick(self, places: list[str], importer: str, specifier: str, directory: bool = False) -> tuple[str | None, str]:
        """(the file the specifier names, "") for an import written in importer, (None, why) when it names a file that is
        not known (the answer depends on a resolver ripple cannot see), or (None, "") when it names no file.

        The compiler tries the TypeScript extensions across every place first and the JavaScript ones after, for any
        importer, so a TypeScript importer reaches the .ts file wherever it is and a .js file only when no .ts file is
        there. A JavaScript importer is run by Node or a bundler, which find the .js file: where both a TypeScript and a
        JavaScript file are named, the two disagree, and so does Vite or Node with the compiler on an extensionless import
        that has twins in another format (x.js and x.mjs). A file counts when ripple reads it or the other language's
        index does (see `_has`); this is the only place a candidate path is tested, so aliases are told as relative
        imports are."""
        js = importer.endswith(JS_SUFFIXES)
        ts_hit, js_hit = self._first(places, False, directory), self._first(places, True, directory)
        if js and ts_hit is not None and js_hit is not None:
            return None, (
                f"`{specifier}` names {ts_hit[0]} to the compiler, which tries TypeScript files first, and {js_hit[0]} to Node "
                "and the bundlers, so which file it names is not known"
            )
        hit = ts_hit or js_hit
        if hit is None:
            # no file, but a directory of the repo is no package and no missing path: what its package.json or a file that is
            # not source names is not known
            named = next((place for place in places if self._directory(place)), None)
            return (None, self._directory_reason(specifier, named)) if named is not None else (None, "")
        file, place = hit
        # a directory the compiler tries before the one the file was found in: its package.json may name another file
        for earlier in places[: places.index(place)]:
            if self._directory(earlier) and self._has_package_json(earlier):
                return None, self._directory_reason(specifier, earlier)
        # the file was reached through a directory (its index), or beside a directory that the compiler's TypeScript pass
        # enters first: either way the directory's package.json (`main`, `types`, `module`, `exports`) picks the file
        if self._directory(place) and self._has_package_json(place) and not (hit is ts_hit and not self._is_index_of(file, place)):
            return None, self._directory_reason(specifier, place)
        if file in self.modules and not place.endswith(SOURCE_EXTENSIONS):
            why = self._disagreement(file, place, directory, js, specifier)
            if why:
                return None, why
        return file, ""

    @staticmethod
    def _is_index_of(file: str, place: str) -> bool:
        """Whether `file` is the index file of the directory `place` (`.` is the root)."""
        return posixpath.dirname(file) == ("" if place == "." else place) and posixpath.basename(file).startswith("index.")

    @staticmethod
    def _directory_reason(specifier: str, place: str) -> str:
        # one wording whether or not a package.json is there: the answer must not depend on a file the cache does not watch (a
        # directory that holds no source has none of its package.json fingerprinted), and either way the entry is not known
        return (
            f"`{specifier}` names the directory {place}/, whose entry its package.json (`main`, `module`, `types`, `exports`) or an index file "
            "ripple does not read chooses, so which file it names is not known"
        )

    def _directory(self, place: str) -> bool:
        """Whether the repo-relative path is a directory of the repo that is not one of a package's (a path through
        node_modules and the like names a package)."""
        if place not in self._directories:
            ok = not place.startswith("..") and not any(part in IMPLICIT_EXCLUDES for part in place.split("/"))
            self._directories[place] = ok and (self.repo / place).is_dir() and _inside_dir(self.repo, place)
        return self._directories[place]

    def _has_package_json(self, place: str) -> bool:
        return _inside(self.repo, posixpath.join("" if place == "." else place, "package.json"))

    def _first(self, places: list[str], js: bool, directory: bool) -> tuple[str, str] | None:
        """(the first file of the compiler's pass over the places, the place it was found at), or None."""
        for place in places:
            hit = next((c for c in _candidates(place, js, directory) if self._has(c)), None)
            if hit is not None:
                return hit, place
        return None

    def _disagreement(self, file: str, base: str, directory: bool, js: bool, specifier: str) -> str:
        """Why an extensionless import that reaches `file` is not known to name it: Vite and Rollup try .mjs first and .js
        before .ts, and for a JavaScript importer Node's require tries .js, so they may name another file the repo has."""
        tried = {"Vite and Rollup": BUNDLER_ORDER, **({"Node's require": NODE_ORDER} if js else {})}
        pre = "" if base == "." else base + "/"
        for who, order in tried.items():
            pick = None if directory or base == "." else next((base + ext for ext in order if self._has(base + ext)), None)
            pick = pick or next((f"{pre}index{ext}" for ext in order if self._has(f"{pre}index{ext}")), None)
            # for a TypeScript importer a .js file of the same name is what the build makes of the source, not a rival to it
            if pick is not None and pick != file and (js or not pick.endswith(JS_SUFFIXES)):
                return f"`{specifier}` names {file} to the compiler and {pick} to {who}, so which file it names is not known"
        return ""

    def _has(self, rel: str) -> bool:
        """Whether the repo-relative path is a file the walk finds, of either language: one ripple reads here (in
        `modules`), or one the other language's index reads. A file the walk skips (node_modules, build output,
        a .d.ts) is no source file, whatever is on disk."""
        return rel in self.modules or rel in self._has_others()

    def _has_others(self) -> set[str]:
        if self._others is None:  # one walk, once, and only when an import reaches no file of this language
            self._others = set(_other_sources(self.repo, self.files))
        return self._others

    def _exists(self, base: str) -> bool:
        try:
            return (self.repo / base).exists()
        except OSError:
            return False
