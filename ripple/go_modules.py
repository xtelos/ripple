"""Which Go package a directory is, and which directory an import path names, read from go.mod and go.work.

A Go package is named by its import path: the path of the module (the `module` line of the nearest go.mod
at or above its directory) plus the directory below that module's root. Symbol ids start with it
(`github.com/spf13/cobra:Command.Execute`), so this is the one place that has to be right about it.

What an import path names is provable only inside what the build would use (cmd/go, module mode):
  - the importer's own module: a path that is the module's path or below it, naming a directory that
    holds a package of that module (a nested go.mod cuts its subtree out of the outer module);
  - another module of this repo only when the importer's go.mod has `replace M => <local directory>` or a
    go.work above lists both modules. Replace directives count only in the main module's go.mod, which for
    a build run inside one module is that module;
  - a module of this repo that is neither replaced nor in the workspace is built from its published
    version, which may differ from the checkout, so an import of it is outside the repo ("published");
  - anything else (the standard library, dependencies) is outside the repo.
A path inside a visible repo module that names no directory ripple read is "missing", which is not
outside: the code exists somewhere ripple did not look, so what the import gives is not known.

Nothing here needs the Go toolchain or a parser: go.mod and go.work are read as text.
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


def is_ignored_dir(name: str) -> bool:
    return name in SKIPPED_DIRS or name.startswith((".", "_"))


def _unquote(word: str) -> str:
    return word[1:-1] if len(word) >= 2 and word[0] == word[-1] and word[0] in "\"`" else word


def _words(line: str) -> list[str]:
    """The words of one go.mod or go.work line, comments dropped, a quoted word kept whole."""
    return [_unquote(w) for w in re.findall(r"\"[^\"]*\"|`[^`]*`|[^\s()]+", re.sub(r"//.*", "", line))]


def _directives(text: str):
    """(verb, words) for every directive of a go.mod or go.work, a block `verb ( ... )` giving one per line."""
    verb = ""
    for raw in text.splitlines():
        line = re.sub(r"//.*", "", raw).strip()
        if not line:
            continue
        if verb:
            if line.startswith(")"):
                verb = ""
            else:
                yield verb, _words(line)
            continue
        words = _words(line)
        if not words:
            continue
        if line.endswith("("):
            verb = words[0]
        else:
            yield words[0], words[1:]


def _is_local(target: str) -> bool:
    return target.startswith(("./", "../", "/")) or target in (".", "..")


@dataclass
class GoMod:
    """A go.mod: where it is (repo-relative directory, "" for the root), its module path, and its replaces."""

    dir: str
    path: str
    # module path replaced -> repo-relative directory of the local replacement (only `=> ./dir` forms)
    replaces: dict[str, str] = field(default_factory=dict)


def _local_dir(base: str, target: str) -> str | None:
    """The repo-relative directory a local replace or use names, None when it points outside the repo."""
    if target.startswith("/"):
        return None
    joined = os.path.normpath(os.path.join(base or ".", target)).replace("\\", "/")
    return None if joined == ".." or joined.startswith("../") else ("" if joined == "." else joined)


def read_gomod(directory: str, text: str) -> GoMod | None:
    path, replaces = "", {}
    for verb, words in _directives(text):
        if verb == "module" and words and not path:
            path = words[0]
        elif verb == "replace" and "=>" in words:
            cut = words.index("=>")
            old, new = words[:cut], words[cut + 1 :]
            if old and new and _is_local(new[0]):
                local = _local_dir(directory, new[0])
                if local is not None:
                    replaces[old[0]] = local
    return GoMod(directory, path, replaces) if path else None


def read_gowork(directory: str, text: str) -> tuple[set[str], dict[str, str]]:
    """(the repo-relative directories a go.work uses, its local replaces)."""
    uses, replaces = set(), {}
    for verb, words in _directives(text):
        if verb == "use" and words:
            local = _local_dir(directory, words[0])
            if local is not None:
                uses.add(local)
        elif verb == "replace" and "=>" in words:
            cut = words.index("=>")
            if words[:cut] and words[cut + 1 :] and _is_local(words[cut + 1]):
                local = _local_dir(directory, words[cut + 1])
                if local is not None:
                    replaces[words[0]] = local
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
        self.works: dict[str, tuple[set[str], dict[str, str]]] = {}  # directory of the go.work -> (uses, replaces)
        self.files: list[str] = []  # every go.mod and go.work read, repo-relative
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
        for name in ("go.mod", "go.work"):
            text = _read(base / name) if (base / name).is_file() else None
            if text is None:
                continue
            rel = f"{directory}/{name}" if directory else name
            self.files.append(rel)
            if name == "go.mod":
                mod = read_gomod(directory, text)
                if mod is not None:
                    self.mods[directory] = mod
            else:
                self.works[directory] = read_gowork(directory, text)

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

    def _workspace(self, mod: GoMod) -> tuple[set[str], dict[str, str]] | None:
        """The (uses, replaces) of the nearest go.work above a module that lists it, if any."""
        here = mod.dir
        while True:
            if here in self.works and mod.dir in self.works[here][0]:
                return self.works[here]
            if not here:
                return None
            here = _parent(here)

    def resolve(self, importer_dir: str, path: str, package_dirs: set[str]) -> tuple[str, str]:
        """(how, directory) for an import of `path` written in a package at `importer_dir`: how is one of OWN,
        REPLACE, WORK (the repo directory it names, which holds a package) or PUBLISHED, MISSING, OUTSIDE (no
        directory)."""
        own = self.module_of(importer_dir)
        best = None
        for module, dirs in self.by_path.items():
            if (path == module or path.startswith(module + "/")) and (best is None or len(module) > len(best[0])):
                best = (module, dirs)
        if best is None:
            return OUTSIDE, ""
        if own is None:  # a file outside any module: which module the import means is not provable
            return MISSING, ""
        module, dirs = best
        workspace = self._workspace(own)
        replaces = dict(own.replaces)
        if workspace is not None:
            replaces.update(workspace[1])
        # Which module directory the import means: the importer's own, one a replace points at, or a workspace member.
        found, how = None, ""
        if module == own.path and own.dir in dirs:
            found, how = own.dir, OWN
        elif module in replaces and replaces[module] in self.mods and self.mods[replaces[module]].path == module:
            found, how = replaces[module], REPLACE
        elif workspace is not None:
            members = [d for d in dirs if d in workspace[0]]
            if len(members) == 1:
                found, how = members[0], WORK
        if found is None:
            return (PUBLISHED, "") if dirs else (MISSING, "")
        rest = path[len(module) :].lstrip("/")
        target = f"{found}/{rest}".strip("/") if rest else found
        owner = self.module_of(target)
        if owner is None or owner.dir != found or target not in package_dirs:
            return MISSING, ""
        return how, target
