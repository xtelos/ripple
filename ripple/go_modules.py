"""Which Go package a directory is, and which directory an import path names, read from go.mod and go.work.

A Go package is named by its import path: the path of the module (the `module` line of the nearest go.mod
at or above its directory) plus the directory below that module's root. Symbol ids start with it
(`github.com/spf13/cobra:Command.Execute`), so this is the one place that has to be right about it.

WHICH MODULE GOVERNS. What an import path names is decided by the MAIN module of the build, never by the module
the importing file is in: the main module (the module of the program or test binary being built, or the modules of
the go.work workspace) is the only one whose `replace` and `exclude` directives apply, and the `replace` directives of
every other module are ignored. So `resolve_in(main, ...)` answers for one main module, and a closure of imports is
resolved under the module the build runs in, however a dependency's own go.mod reads. Under that main module an
import path names:
  - the main module's own package: a path that is the module's path or below it, naming a directory that holds a
    package of that module (a nested go.mod cuts its subtree out of the outer module);
  - another module of this repo only when the main module has a `replace M => <local directory>` that applies, or a
    go.work lists both (the replaces of every workspace module and of the go.work then apply, the go.work's first);
  - a module of this repo that is neither is built from its published version, which may differ from the checkout, so
    an import of it is outside the repo ("published");
  - anything else (the standard library, dependencies) is outside the repo.
A `replace M v1.2.0 => ./b` applies only when the build selects exactly v1.2.0. What is selected needs minimal version
selection over the module graph, which is not read here, with one exception: a main module that states `go 1.17` or
later lists in its go.mod the selected version of every module that provides a package (a build that would need
another version fails with "updates to go.mod needed"), so the `require` line is the selected version, unless that
version is excluded. Otherwise (an older go directive, a workspace, a version in neither place) a versioned replace is
not known to apply, and the import is "missing": it may be the checkout or a published version, so it is neither
repo code nor outside code. A main module with a committed vendor/modules.txt builds from vendor/ by default (and a
GOFLAGS ripple cannot see may say otherwise), so every import of another module is "missing" there.
A path inside a visible repo module that names no directory ripple read is "missing" too: the code exists somewhere
ripple did not look, so what the import gives is not known.

`resolve` is the importing file's own module as the main module: a build run in that module's directory exists, so it
is what a call edge is drawn from; `resolve_agreed` demands that every module whose build contains that file resolves
the import alike.

Nothing here needs the Go toolchain or a parser: go.mod, go.work and vendor/modules.txt are read as text.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# A package directory the go tool does not walk into: vendored copies, test data, and (with a leading `.`
# or `_`) anything it is told to ignore. testdata stays importable by path, but is never read here.
SKIPPED_DIRS = ("vendor", "testdata")

OWN, REPLACE, WORK, PUBLISHED, MISSING, OUTSIDE = "own", "replace", "work", "published", "missing", "outside"

# What a replace directive says of one module path: {the version on its left ("" for every version): the repo directory
# of the local replacement, None for a replacement that is not a directory of this repo (a module, a path outside it)}.
Replaces = dict[str, dict[str, str | None]]
CONFLICT = "\0conflict"  # a workspace whose modules replace one module differently: go refuses to build it


def is_ignored_dir(name: str) -> bool:
    return name in SKIPPED_DIRS or name.startswith((".", "_"))


class _Paren:
    def __init__(self, text: str):
        self.text = text


OPEN, CLOSE = _Paren("("), _Paren(")")
TOKEN = re.compile(r'\s*(?:(//[^\n]*)|"((?:[^"\\\n]|\\.)*)"|`([^`\n]*)`|(=>)|([()])|((?:(?!=>|//)[^\s()"`])+))')


def _tokens(line: str) -> list:
    """The tokens of one go.mod or go.work line as the go.mod grammar reads them: a word, a quoted word (an interpreted
    string with its escapes, or a raw one) kept whole, `=>`, the parentheses of a block; a `//` comment ends the line."""
    found, at = [], 0
    while at < len(line):
        match = TOKEN.match(line, at)
        if match is None:
            break
        at = match.end()
        comment, quoted, raw, arrow, paren, word = match.groups()
        if comment is not None:
            break
        if quoted is not None:
            found.append(re.sub(r"\\(.)", r"\1", quoted))
        elif raw is not None:
            found.append(raw)
        elif arrow is not None:
            found.append("=>")
        elif paren is not None:
            found.append(OPEN if paren == "(" else CLOSE)
        else:
            found.append(word)
    return found


def _directives(text: str):
    """(verb, words) for every directive of a go.mod or go.work: a one-line directive, or each line of a block
    `verb ( ... )`, comments dropped. The verbs ripple does not read (retract, tool, godebug, ...) pass through whole."""
    verb = ""
    for raw in text.splitlines():
        tokens = _tokens(raw)
        if not tokens:
            continue
        if verb:
            if tokens[0] is CLOSE:
                verb = ""
            else:
                yield verb, [t for t in tokens if not isinstance(t, _Paren)]
            continue
        if isinstance(tokens[0], _Paren):
            continue
        if len(tokens) == 2 and tokens[1] is OPEN:
            verb = tokens[0]
        else:
            yield tokens[0], [t for t in tokens[1:] if not isinstance(t, _Paren)]


def _is_local(target: str) -> bool:
    return target.startswith(("./", "../", "/")) or target in (".", "..")


@dataclass
class GoMod:
    """A go.mod: where it is (repo-relative directory, "" for the root), its module path, and what it says that a build
    run in it needs: its replaces (see Replaces), its go directive as (major, minor), the versions it requires of each
    module, the versions it excludes, and whether a committed vendor/modules.txt sits beside it."""

    dir: str
    path: str
    replaces: Replaces = field(default_factory=dict)
    go: tuple[int, int] | None = None
    requires: dict[str, set[str]] = field(default_factory=dict)
    excludes: set[tuple[str, str]] = field(default_factory=set)
    vendored: bool = False


def _local_dir(base: str, target: str) -> str | None:
    """The repo-relative directory a local replace or use names, None when it points outside the repo."""
    if target.startswith("/"):
        return None
    joined = os.path.normpath(os.path.join(base or ".", target)).replace("\\", "/")
    return None if joined == ".." or joined.startswith("../") else ("" if joined == "." else joined)


def _replace(directory: str, words: list[str], into: Replaces) -> None:
    """One `replace old [version] => new [version]` directive: `new` is a repo directory only when it is a path (a module
    path with a version is a replacement from the network, which is not the checkout)."""
    if "=>" not in words:
        return
    cut = words.index("=>")
    old, new = words[:cut], words[cut + 1 :]
    if not 1 <= len(old) <= 2 or not 1 <= len(new) <= 2:
        return
    local = _local_dir(directory, new[0]) if len(new) == 1 and _is_local(new[0]) else None
    into.setdefault(old[0], {})[old[1] if len(old) == 2 else ""] = local


def _go_version(words: list[str]) -> tuple[int, int] | None:
    match = re.match(r"(\d+)\.(\d+)", words[0]) if words else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def read_gomod(directory: str, text: str) -> GoMod | None:
    mod = GoMod(directory, "")
    for verb, words in _directives(text):
        if verb == "module" and words and not mod.path:
            mod.path = words[0]
        elif verb == "go" and mod.go is None:
            mod.go = _go_version(words)
        elif verb == "replace":
            _replace(directory, words, mod.replaces)
        elif verb == "require" and len(words) >= 2:
            mod.requires.setdefault(words[0], set()).add(words[1])
        elif verb == "exclude" and len(words) >= 2:
            mod.excludes.add((words[0], words[1]))
    return mod if mod.path else None


def read_gowork(directory: str, text: str) -> tuple[set[str], Replaces]:
    """(the repo-relative directories a go.work uses, its replaces)."""
    uses: set[str] = set()
    replaces: Replaces = {}
    for verb, words in _directives(text):
        if verb == "use" and words:
            local = _local_dir(directory, words[0])
            if local is not None:
                uses.add(local)
        elif verb == "replace":
            _replace(directory, words, replaces)
    return uses, replaces


def _parent(directory: str) -> str:
    return directory.rpartition("/")[0]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class Modules:
    """The go.mod and go.work files above a repo's Go source directories, and what they say."""

    def __init__(self, repo: Path, directories: set[str]):
        self.repo = repo
        self.mods: dict[str, GoMod] = {}  # repo-relative directory of the go.mod -> it
        self.works: dict[str, tuple[set[str], Replaces]] = {}  # directory of the go.work -> (uses, replaces)
        self.work_vendored: set[str] = set()  # the directories of a go.work that has a vendor/modules.txt beside it
        self.files: list[str] = []  # every go.mod, go.work and vendor/modules.txt read, repo-relative
        self._governors: dict[str, list[GoMod]] = {}
        self._workspaces: dict[str, tuple[set[str], Replaces, bool]] = {}
        seen: set[str] = set()
        for directory in sorted(directories):
            here = directory
            while here not in seen:
                seen.add(here)
                self._read_dir(here)
                if not here:
                    break
                here = _parent(here)
        self.by_path: dict[str, list[str]] = {}
        for mod in self.mods.values():
            self.by_path.setdefault(mod.path, []).append(mod.dir)

    def _read_dir(self, directory: str) -> None:
        base = self.repo / directory if directory else self.repo
        vendor = (base / "vendor" / "modules.txt").is_file()
        found = False
        for name in ("go.mod", "go.work"):
            text = _read(base / name) if (base / name).is_file() else None
            if text is None:
                continue
            found = True
            self.files.append(f"{directory}/{name}" if directory else name)
            if name == "go.mod":
                mod = read_gomod(directory, text)
                if mod is not None:
                    mod.vendored = vendor
                    self.mods[directory] = mod
            else:
                self.works[directory] = read_gowork(directory, text)
                if vendor:
                    self.work_vendored.add(directory)
        if found and vendor:
            self.files.append(f"{directory}/vendor/modules.txt" if directory else "vendor/modules.txt")

    def module_of(self, directory: str) -> GoMod | None:
        """The module whose go.mod is the nearest at or above a repo-relative directory."""
        here = directory
        while True:
            if here in self.mods:
                return self.mods[here]
            if not here:
                return None
            here = _parent(here)

    def import_path(self, directory: str) -> str | None:
        """The import path of the package in a directory, None for a directory outside any module."""
        mod = self.module_of(directory)
        if mod is None:
            return None
        below = directory[len(mod.dir) :].lstrip("/") if mod.dir else directory
        return f"{mod.path}/{below}" if below else mod.path

    def workspace_dir(self, mod: GoMod) -> str | None:
        """The directory of the nearest go.work above a module that lists it, if any."""
        here = mod.dir
        while True:
            if here in self.works and mod.dir in self.works[here][0]:
                return here
            if not here:
                return None
            here = _parent(here)

    def _workspace(self, mod: GoMod) -> tuple[set[str], Replaces, bool] | None:
        """(uses, replaces, vendored) of the workspace a module is a member of: the replaces of every member's go.mod and
        then the go.work's own (which win); a module replaced differently by two members is a CONFLICT, which go refuses."""
        where = self.workspace_dir(mod)
        if where is None:
            return None
        if where in self._workspaces:
            return self._workspaces[where]
        uses, own = self.works[where]
        replaces: Replaces = {}
        for member in sorted(uses):
            for path, versions in (self.mods[member].replaces if member in self.mods else {}).items():
                for version, local in versions.items():
                    entry = replaces.setdefault(path, {})
                    entry[version] = CONFLICT if version in entry and entry[version] != local else local
        for path, versions in own.items():
            replaces.setdefault(path, {}).update(versions)
        self._workspaces[where] = (uses, replaces, where in self.work_vendored)
        return self._workspaces[where]

    def run_key(self, mod: GoMod) -> str:
        """What one build's main module is: the workspace directory when the module is a member of a go.work (all its
        members are main modules of one build and resolve alike), the module's own directory otherwise."""
        where = self.workspace_dir(mod)
        return f"work:{where}" if where is not None else f"mod:{mod.dir}"

    def builds_against(self, importer: GoMod, target: GoMod) -> bool:
        """Whether a build run in the importer module has the checkout of the target module: it is the same module, or the
        importer's go.mod (or a go.work above both) replaces the target's path with that directory. This is the rule
        `resolve_in` reads an import by, asked of the module and not of one import."""
        if importer is target:
            return True
        how, found = self.resolve_in(importer, target.path, {target.dir})
        return how in (OWN, REPLACE, WORK) and found == target.dir

    def governors(self, mod: GoMod) -> list[GoMod]:
        """The modules some build run in which has the checkout of this module: itself, and every module that replaces it
        with its directory or shares a go.work with it. A file of the module is in the builds of every one of them."""
        if mod.dir not in self._governors:
            self._governors[mod.dir] = [m for m in self.mods.values() if self.builds_against(m, mod)]
        return self._governors[mod.dir]

    def selected(self, main: GoMod, module: str) -> str | None:
        """The version of a module that a build run in `main` selects, when go.mod states it: see the module docstring."""
        if self.workspace_dir(main) is not None or main.go is None or main.go < (1, 17):
            return None
        versions = main.requires.get(module, set())
        if len(versions) != 1:
            return None
        (version,) = versions
        return None if (module, version) in main.excludes else version

    def resolve(self, importer_dir: str, path: str, package_dirs: set[str]) -> tuple[str, str]:
        """(how, directory) for an import of `path` written in a package at `importer_dir`, resolved as a build run in the
        importer's own module sees it (see the module docstring); how is one of OWN, REPLACE, WORK (the repo directory it
        names, which holds a package) or PUBLISHED, MISSING, OUTSIDE (no directory)."""
        return self.resolve_in(self.module_of(importer_dir), path, package_dirs)

    def resolve_agreed(self, importer_dir: str, path: str, package_dirs: set[str]) -> tuple[str, str]:
        """`resolve`, but only when every module whose build contains a file of the importer's module resolves the path to
        the same repo directory (or to no repo directory): a file that one build links against the checkout and another
        against a published version has no one callee."""
        own = self.module_of(importer_dir)
        first = self.resolve_in(own, path, package_dirs)
        if own is None or first[0] == OUTSIDE:
            return first
        for other in self.governors(own):
            if other is own:
                continue
            how, found = self.resolve_in(other, path, package_dirs)
            if how == MISSING or (found or "") != (first[1] or "") or (how == OUTSIDE) != (first[0] == OUTSIDE):
                return MISSING, ""
        return first

    def resolve_in(self, main: GoMod | None, path: str, package_dirs: set[str]) -> tuple[str, str]:
        """`resolve` for a build whose main module is `main`: its replaces and excludes, or the workspace it is in, decide."""
        best = None
        for module, dirs in self.by_path.items():
            if (path == module or path.startswith(module + "/")) and (best is None or len(module) > len(best[0])):
                best = (module, dirs)
        if best is None:
            return OUTSIDE, ""
        if main is None:  # a file outside any module: which module the import means is not provable
            return MISSING, ""
        module, dirs = best
        workspace = self._workspace(main)
        found, how = None, ""
        members = [d for d in dirs if workspace is not None and d in workspace[0]]
        if module == main.path and main.dir in dirs:
            found, how = main.dir, OWN
        elif (workspace[2] if workspace is not None else main.vendored):
            if len(members) != 1:
                return MISSING, ""  # built from vendor/ (or not, under a GOFLAGS ripple cannot see): neither the checkout nor outside
            found, how = members[0], WORK
        else:
            entries = (workspace[1] if workspace is not None else main.replaces).get(module, {})
            key = None
            if entries:
                version = self.selected(main, module)
                if version is not None:
                    key = version if version in entries else ("" if "" in entries else None)
                elif set(entries) == {""}:
                    key = ""
                else:
                    return MISSING, ""  # a versioned replace that may or may not be the one the build selects
            if key is not None:
                local = entries[key]
                if local == CONFLICT:
                    return MISSING, ""
                if local is not None and local in self.mods and self.mods[local].path == module:
                    found, how = local, REPLACE
            elif len(members) == 1:
                found, how = members[0], WORK
        if found is None:
            return (PUBLISHED, "") if dirs else (MISSING, "")
        rest = path[len(module) :].lstrip("/")
        target = f"{found}/{rest}".strip("/") if rest else found
        owner = self.module_of(target)
        if owner is None or owner.dir != found or target not in package_dirs:
            return MISSING, ""
        return how, target
