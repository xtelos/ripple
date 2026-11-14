"""Which crates a Rust repo holds, what each is called, and which file is each of its modules.

A module is its file path without the extension, with `x/mod.rs` standing for `x` (`src/cli`, and
`src/lib` for the crate root, which is never folded: a package may hold both a lib.rs and a main.rs).
A symbol inside it follows a colon, its own path inside the file after that (`src/cli:parse_args`,
`src/cli:Opts::new`, `src/cli:tests::it_works`).

Cargo.toml is read for the crate graph: every package, the crates it builds (the library, the binaries,
tests, examples, benches and build script, found as cargo finds them, with `[lib]` and `[[bin]]` path
overrides), and what each package can name by `use`: its dependencies by the name they are written
under (a hyphen is an underscore, a renamed one is its key), and which of them are packages of this
repo (a `path` dependency, a `workspace = true` one that the workspace gives a path). Everything else a package names is outside the
repo, unless a `[patch]`, a `[replace]` or a config's `paths` may redirect it: then it is unresolved, never an edge and never external (Rule P,
Redirects). A `path` dependency is an edge unless one of those may redirect it too.

The module tree of a crate starts at its root file and follows `mod` declarations: `mod x;` is x.rs
or x/mod.rs beside the declaring file (or inside the directory a non-mod.rs file owns), `#[path]`
names the file outright, and `mod x { ... }` is a module in the same file. A declaration written by
a macro, under a `cfg_attr(.., path = ..)`, or naming no file leaves its file out, and says so
(Tree.problems), as does a file the walk did not list. No parser is needed here: the tree is built from
the nodes a caller's `parse` hands back, and Cargo.toml needs only tomllib (tomli before Python 3.11).
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
from collections import ChainMap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

SUFFIX = ".rs"
STD_CRATES = frozenset({"std", "core", "alloc", "proc_macro", "test"})
AMBIGUOUS = "?"  # a dependency name two sections of one manifest give different meanings
MAX_DEPTH = 60  # nested mod declarations followed (a cycle through #[path] stops here)


def stem(rel: str) -> str:
    """A file path without its `.rs`."""
    return rel[: -len(SUFFIX)] if rel.endswith(SUFFIX) else rel


def module_id(rel: str) -> str:
    """The module a source file (a repo-relative posix path) is: no extension, `mod.rs` folded into its directory."""
    name = stem(rel)
    head, _, tail = name.rpartition("/")
    return head if tail == "mod" and head else name


def _source_dirs(repo: Path, sources: list[Path]) -> set[str]:
    """Every directory that holds or sits above a source file, repo-relative ("" is the repo)."""
    dirs: set[str] = {""}
    for path in sources:
        try:
            rel = path.relative_to(repo).parent
        except ValueError:
            continue
        for parent in (rel, *rel.parents):
            dirs.add("" if parent == Path(".") else parent.as_posix())
    return dirs


def manifests(repo: Path, sources: list[Path]) -> list[str]:
    """Every Cargo.toml in a directory that holds or sits above a source file, repo-relative. A package's own
    manifest and its workspace's are among these; a manifest above no source file makes no package ripple reads (but `read_redirects`
    reads it for a `[patch]`, and `config_files` has it)."""
    return sorted(
        (f"{d}/Cargo.toml" if d else "Cargo.toml") for d in _source_dirs(repo, sources) if (repo / d / "Cargo.toml").is_file()
    )


CARGO_CONFIGS = (".cargo/config.toml", ".cargo/config")


def config_files(repo: Path, sources: list[Path]) -> list[str]:
    """The files besides the sources that the index depends on, which the cache fingerprints (registry.py): the manifests, the
    Cargo.lock beside each, every cargo config file `cargo_configs` finds (what `_trust` reads to prove an attribute macro is the
    published crate's), every Cargo.toml the walk of the repo finds (read_redirects looks in each for a `[patch]` or a `[replace]`, in a
    directory with no source file too), the Cargo.toml of each directory above the repo (whether there is one), the Cargo.toml of
    each path dependency outside the repo, and the manifest of each path dependency inside the repo that the walk gave no source file to
    (`_extra_manifests`, read for the requirements that decide the lock)."""
    read = manifests(repo, sources)
    out = list(read)
    for d in _source_dirs(repo, sources):
        lock = _join(d, "Cargo.lock")
        if (repo / d / "Cargo.toml").is_file() and (repo / lock).is_file():
            out.append(lock)
    configs, found = _walk(repo)
    data, unreadable, _ = _read_manifests(repo, read)
    out += _extra_manifests(repo, data, unreadable)[1]
    return sorted({*out, *configs, *found, *above_manifests(repo), *outside_manifests(repo, sources)})


def _toml():
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: the backport the extra installs
        import tomli as tomllib
    return tomllib


def _join(*parts: str) -> str:
    """A repo-relative path from parts, normalised ("" for the repo root, which normpath writes as ".")."""
    joined = posixpath.normpath(posixpath.join(*parts)) if any(parts) else ""
    return "" if joined == "." else joined


@dataclass
class Package:
    dir: str  # repo-relative, "" for the root
    name: str
    lib_name: str | None  # the library crate's name, None when the package has no library
    deps: dict[str, str | None]  # extern name -> the dir of the repo package it is, None when it is outside the repo, AMBIGUOUS or PATCH_UNPROVEN when ripple cannot say which
    manifest: dict
    edition: str
    trust: dict[str, str | None] = field(default_factory=dict)  # crate of TRUSTED_CRATES -> None when the repo proves it the published one, else why not
    # What `deps` does not say a dependency of the package may be, for the question of what `std` and `core` name in its crates (Index.foreign_crates):
    # the dir of the repo package a patch may make a dependency (None: a package ripple cannot read, which a patch to another path or source, a path
    # outside the repo, or a patch it does not follow makes). `std_why` is why anything of the package may be another crate, from what governs it as a
    # whole: a cargo config of the repo that may make `std` another crate or `rustc` another compiler (STD_CONFIG_KEYS, not the trust proof's
    # stricter set), or a `[replace]` entry.
    maybe: tuple[str | None, ...] = ()
    std_why: str | None = None
    # the library names of the path dependencies outside the analysed directory, read from their Cargo.toml alone (_outside_lib): what the package can
    # name beside its other dependencies. An outside dependency whose Cargo.toml cannot be read is a None in `maybe` instead.
    outside_libs: tuple[str, ...] = ()
    requirements: dict[str, tuple[str | None, ...]] = field(default_factory=dict)  # the version requirement of each crate in REQUIRED_CRATES, as every package the lock covers writes it (the lock's workspace and the closure of their path dependencies; _requirements; a dependency key written however, None for one ripple cannot read as a version string)
    usable_lock: dict[str, tuple[str, ...]] | None = None  # memo of rs_indexer.usable_locked: the lock's versions of each macro crate it still holds for
    locked: dict[str, tuple[str, ...]] | None = None  # the Cargo.lock of the package's workspace: each package name (hyphens as underscores) -> the versions it lists; None without a lock or with one that cannot be read


KNOWN_EDITIONS = frozenset({"2015", "2018", "2021", "2024"})


@dataclass
class Crate:
    name: str
    kind: str  # lib | bin | test | example | bench | build
    root: str  # repo-relative file
    package: Package
    edition: str | None = None  # the `edition` the target's own table sets (`[lib] edition = "2015"`), None when it sets none and the package's is the crate's

    @property
    def edition_year(self) -> int:
        """The edition rustc builds this crate in: the target's own, else the package's. An edition ripple does not know (a value cargo
        rejects, one newer than it has read) reads as 2015, the one every edition rule refuses on."""
        written = self.package.edition if self.edition is None else self.edition
        return int(written) if written in KNOWN_EDITIONS else 2015


@dataclass
class Workspace:
    packages: dict[str, Package] = field(default_factory=dict)  # dir -> package
    crates: list[Crate] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # manifests that could not be read


def _read_manifests(repo: Path, rels: list[str]) -> tuple[dict[str, dict], set[str], list[str]]:
    """(directory -> its parsed Cargo.toml, the directories whose Cargo.toml could not be read, a problem line for each of those)."""
    toml = _toml()
    data: dict[str, dict] = {}
    unreadable: set[str] = set()
    problems: list[str] = []
    for rel in rels:
        try:
            data[posixpath.dirname(rel)] = toml.loads((repo / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            problems.append(f"{rel} (Cargo.toml could not be read)")
            unreadable.add(posixpath.dirname(rel))
    return data, unreadable, problems


def load_workspace(repo: Path, sources: list[Path]) -> Workspace:
    """Every package and crate of the repo, read from the Cargo.toml files above its sources."""
    ws = Workspace()
    # `unreadable`: the directories whose Cargo.toml could not be read: nothing at or below them is proven
    data, unreadable, ws.problems = _read_manifests(repo, manifests(repo, sources))
    extras, _, members = _extra_manifests(repo, data, unreadable)  # the manifests of packages the walk gave no source file to (requirements only)
    known = {d for d in data if isinstance(data[d].get("package"), dict)}
    configs, found = _walk(repo)
    redirects = read_redirects(repo, data, known, unreadable, configs, found)
    for pdir in sorted(data):
        if isinstance(data[pdir].get("package"), dict) and isinstance(data[pdir]["package"].get("name"), str):
            try:
                ws.packages[pdir] = _package(repo, pdir, data, redirects)
            except (AttributeError, TypeError, ValueError, KeyError) as error:
                ws.problems.append(_wrong_type(pdir, error))
    names = {_norm(p.name) for p in ws.packages.values()} | {_norm(m["package"]["name"]) for m in extras.values() if _named(m)}
    libs, libs_why = _library_names(ws, data, unreadable, extras)
    # the config files are read once, for both questions. The trust half needs no gate on "a package names a trusted crate": a package that
    # names none gets "does not depend on" as its trust reason first (_trust), so the config's reason is never the one shown
    config_why, config_std = _config_problems(repo, configs)
    for pdir, package in ws.packages.items():
        package.std_why = package.std_why or (f"a cargo config of the repo may change std or rustc: {config_std}" if config_std else None) or _unread_above(pdir, unreadable)
    locks: dict[str, object] = {}  # workspace root -> its Cargo.lock as _lock_packages reads it, once for all the packages it holds
    wanted: dict[str, dict] = {}  # the same key -> the requirements its packages write (_requirements)
    for pdir, package in ws.packages.items():
        try:
            package.trust = _trust(repo, pdir, data, names, unreadable, config_why, locks, libs, libs_why)
            package.locked = _lock_versions(locks[_lock_base(pdir, data)])
            base = _lock_base(pdir, data)
            if base not in wanted:
                wanted[base] = _requirements(data, base, extras, set(ws.packages), members.get(base, ()))
            package.requirements = wanted[base]
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            package.trust = {c: f"the manifest could not be read for this ({type(error).__name__})" for c in TRUSTED_CRATES}
    for pdir, package in list(ws.packages.items()):
        try:
            ws.crates += _targets(repo, package)
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            ws.problems.append(_wrong_type(pdir, error))
            del ws.packages[pdir]
    return ws


def _unread_above(pdir: str, unreadable: set[str]) -> str | None:
    """Why a manifest that governs the package (its own, or one above it that may hold a `[patch]` or a `[workspace]`) could not be read."""
    here = pdir
    while True:
        if here in unreadable:
            return f"{_join(here, 'Cargo.toml')} could not be read"
        if not here:
            return None
        here = posixpath.dirname(here)


def _wrong_type(pdir: str, error: Exception) -> str:
    """A manifest cargo would reject (a field of the wrong type) costs its package: its crates are not read, and a package
    that depends on it finds its library unread. Never the rest of the repo."""
    manifest = f"{pdir}/Cargo.toml" if pdir else "Cargo.toml"
    return f"{manifest} (a field has the wrong type, so this package is not read: {type(error).__name__}: {error})"


def _workspace_root(pdir: str, data: dict[str, dict]) -> str | None:
    """The directory of the manifest with a `[workspace]` table that governs a package: the one its `workspace`
    key names, else the nearest one at or above it."""
    named = data[pdir].get("package", {}).get("workspace")  # a package table, as the caller has checked
    if isinstance(named, str) and _join(pdir, named) in data:
        return _join(pdir, named)
    here = pdir
    while True:
        if here in data and "workspace" in data[here]:
            return here
        if not here:
            return None
        here = posixpath.dirname(here)


def _package(repo: Path, pdir: str, data: dict[str, dict], redirects: Redirects) -> Package:
    manifest = data[pdir]
    meta = manifest["package"]
    root = _workspace_root(pdir, data)
    shared = data[root].get("workspace", {}) if root is not None else {}
    shared = shared if isinstance(shared, dict) else {}
    shared_deps = shared.get("dependencies", {}) if isinstance(shared.get("dependencies"), dict) else {}
    top = data[root] if root is not None else manifest  # a package no `[workspace]` table governs is a workspace root of its own (probe manifest_patch_std)
    known = {d for d in data if isinstance(data[d].get("package"), dict)}  # the dir a path names must be a package of the repo
    # a `package.workspace` naming a manifest ripple did not read: the root that governs the package may hold a patch ripple cannot see
    named = meta.get("workspace")
    unread_root = named is not None and not (isinstance(named, str) and _join(pdir, named) in redirects.read)
    # a manifest that governs the package could not be read: its own or one above it, or the root `package.workspace` names when that root is
    # inside the analysed directory (a root outside it is the README's stated assumption for a path dependency, and Rule P (2) for the rest)
    unread_here = _unread_above(pdir, redirects.unreadable) is not None or (
        unread_root and not (isinstance(named, str) and _leaves_repo(_join(pdir, named)))
    )
    edition = meta.get("edition", "2015")
    if isinstance(edition, dict):  # edition.workspace = true
        shared_package = shared.get("package", {})
        edition = shared_package.get("edition", "2015") if isinstance(shared_package, dict) else "2015"
    deps: dict[str, str | None] = {}

    def add(key: str, target: str | None) -> None:
        name = key.replace("-", "_")
        if name in deps and deps[name] != target:
            deps[name] = AMBIGUOUS
        else:
            deps[name] = target

    maybe: list[str | None] = []
    outside_libs: list[str] = []
    for section in _dependency_sections(manifest):
        if not isinstance(section, dict):
            continue
        for key, spec in section.items():
            real, path, source = key, None, spec if isinstance(spec, dict) else {}
            if isinstance(spec, dict):
                real = spec.get("package", key)
                if spec.get("workspace") and isinstance(shared_deps.get(key), dict):
                    wide = shared_deps[key]
                    real, source = wide.get("package", real), wide
                    if isinstance(wide.get("path"), str) and root is not None:
                        path = _join(root, wide["path"])
                elif isinstance(spec.get("path"), str):
                    path = _join(pdir, spec["path"])
            redirected = redirects.redirected((key, real), unread_root, path is not None, unread_here)
            if redirected is not None:  # a registry or git source, or a path, that something may redirect (a patch, a replace, a `paths`...): Rule P
                add(key, PATCH_UNPROVEN)
                maybe += redirected
            else:  # a path dependency nothing names, or the fetched crate
                add(key, path if path in known else None)
            if isinstance(source.get("path"), str) and path not in known:
                # a path dependency that is no package ripple read. Outside the analysed directory its Cargo.toml (and nothing else of it) says what
                # its library is called, which is all the std question needs; where that cannot be read, or the path is a directory of the repo
                # that holds no package ripple read, a library ripple cannot read: it may be called std. (A git or a registry source is no
                # such dependency: the README assumes it does not name its library std, core or alloc.)
                lib = _outside_lib(repo, path) if path is not None and _leaves_repo(path) else None
                if lib is None:
                    maybe.append(None)
                else:
                    outside_libs.append(lib)
    replaced = "a [replace] entry" if manifest.get("replace") or top.get("replace") else None
    lib = manifest.get("lib")
    lib_name = None  # a package with no [lib] table has a library only if src/lib.rs exists (see _targets)
    if "lib" in manifest:
        lib_name = str(lib.get("name", meta["name"]) if isinstance(lib, dict) else meta["name"]).replace("-", "_")
    return Package(pdir, meta["name"], lib_name, deps, manifest, str(edition), maybe=tuple(dict.fromkeys(maybe)), std_why=replaced, outside_libs=tuple(dict.fromkeys(outside_libs)))


# ---- Rule P: which dependencies a `[patch]`, a `[replace]` or a `paths` may redirect
#
# Whether cargo applies a patch depends on the source it patches, the version, a Cargo.lock, which root governs the package, `members` against
# `exclude`, a `package.workspace`, a versionless git dependency taking a pre-release: tests/test_rs_patch_probes.py builds each, and ripple does
# not follow them. A dependency is PATCH_UNPROVEN, never an edge through a redirect and never external, when it MAY be redirected. The redirects
# cargo accepts, and what ripple reads of each (tests/test_rs_redirect_reach.py, probes in crates/_redirect_reach, each built with cargo):
#
#   A dependency that is not a `path` dependency (a registry or git one):
#   (1) some entry of any `[patch.<table>]` or `[replace]` ripple can read in the analysed directory (every manifest, every cargo config of it)
#       names its package, whether or not cargo would apply that entry; or a table ripple cannot parse is there at all;
#   (2) a manifest that may govern the package was not read and the package of the repo of that name is what an unseen patch could point at (a
#       patch supplies a dependency only with the package of the dependency's own name).
#   A `path` dependency (path_names): named by an entry of a `[patch.<key>]` whose key may be the url of a path
#   source (any key but `crates-io` and an http, https, ssh or git url: a `file:` url is one, and a registry name stands for an index url ripple
#   does not follow); by an entry of a `[replace]` whose key may be the id of a path source (it holds `file:` or `path+`).
#
# And everything is unresolved, path dependencies too, when ripple sees a redirect it cannot read or follow: any `paths` entry of a cargo config
# (cargo loads every package below it and the path dependencies of each, transitively, wherever they point, and overrides by name: ripple follows
# none of it, so the entry refuses, whatever it holds; `paths = []` is no entry), an `include` of a cargo config (the included file is not read),
# a config or a manifest that governs the package and cannot be read, a `package.workspace` root inside the analysed directory that cannot be read
# (for a path dependency: for a registry or git dependency an unreadable config or manifest refuses only as (2) says). This holds for the configs
# ripple's walk reads: it enters no `.git`, `node_modules` or `__pycache__`, and no `target` beside a Cargo.toml, so a config there is not seen
# (`_walk`; README, the assumption on the walk of the configs). Otherwise it is the fetched crate (outside the repo) or the package the path names.

PATCH_UNPROVEN = "?patch"  # a dependency something may redirect to another package than its manifest names: unresolved
UNRESOLVED_DEPS = (AMBIGUOUS, PATCH_UNPROVEN)  # a Package.deps value that is no dir and no None


def _canon(name: str) -> str:
    """A package name as a patch entry and a dependency may spell it alike: a hyphen is an underscore, and case does not tell two apart."""
    return name.replace("-", "_").lower()


@dataclass
class Redirects:
    """What the `[patch]` and `[replace]` entries and the manifests ripple read say of which dependency may be redirected, and what ripple saw of
    a redirect and could not read or follow (a `paths` entry, an `include`: `unparsable`, `path_unparsable`) (Rule P above).

    `names`: a package name some entry names -> the dirs of the packages of the repo its entries' paths name (None: a path that is no package
    ripple read, a git source, no path). `path_names`: the same for the entries that can redirect a PATH dependency. `unparsable`: why some table
    cannot be read, which makes every non-path dependency a candidate; `path_unparsable`: why some redirect cannot be read that may be one of a path
    dependency too. `unread`: why some manifest that may govern a package was not read (a Cargo.toml above the analysed directory or one that could
    not be read, a cargo config that could not be read); `unreadable`: the dirs of the manifests that could not be read. `packages`: the name of each
    package of the repo -> its dirs (None: one ripple did not read as above a source file). `read`: the dirs of the manifests read, for a
    `package.workspace` to name."""

    names: dict[str, set[str | None]] = field(default_factory=dict)
    path_names: dict[str, set[str | None]] = field(default_factory=dict)
    unparsable: str | None = None
    path_unparsable: str | None = None
    unread: str | None = None
    unreadable: set[str] = field(default_factory=set)
    packages: dict[str, set[str | None]] = field(default_factory=dict)
    read: set[str] = field(default_factory=set)

    def redirected(self, written: tuple[str, ...], unread_root: bool, is_path: bool = False, unread_here: bool = False) -> list[str | None] | None:
        """None when nothing can redirect a dependency of this package name (its key and its `package`), else the dirs of the packages of the repo
        it may then be, None among them where ripple cannot place one (that is what the question of `std` asks, Package.maybe). `is_path`: a `path`
        dependency, which only `path_names` and an unreadable redirect can reach; `unread_here`: a manifest that governs the package could not be read."""
        # `{*written}` raises TypeError for a `package` that is a list or a table, as the lookup of it always did (the manifest is one cargo rejects, and
        # load_workspace says the package is not read: test_rs_malformed bad_dep_package); a number is tolerated as before (bad_deps) and names nothing
        names = {_canon(name) for name in {*written} if isinstance(name, str)}
        if is_path:
            if self.path_unparsable is not None or unread_here:
                return [None]
            named = names & self.path_names.keys()
            found: set[str | None] = set().union(*(self.path_names[name] for name in named))
            return sorted(found, key=lambda dir_: (dir_ is None, dir_ or "")) if named else None
        if self.unparsable is not None:
            return [None]
        found = set()
        named = names & self.names.keys()
        for name in named:
            found |= self.names[name]
        unseen = names & self.packages.keys() if self.unread is not None or unread_root else set()
        for name in unseen:
            found |= self.packages[name]
        return sorted(found, key=lambda dir_: (dir_ is None, dir_ or "")) if named or unseen else None


def _refuse(out: Redirects, why: str, path_too: bool = False) -> None:
    """Something of the redirects ripple cannot read: every non-path dependency is a candidate, and every path dependency too when `path_too`."""
    out.unparsable = out.unparsable or why
    if path_too:
        out.path_unparsable = out.path_unparsable or why


def _patch_may_be_path(key: str) -> bool:
    """Whether a `[patch.<key>]` table may be the one of a path source. Cargo matches a patch to a dependency by the source's url, and a path
    source's is its `file:` url; `crates-io` and an http, https, ssh or git url are never that. A registry name (an index url from `[registries]`,
    which may be in a config ripple cannot see), a `file:` url and anything else are not proven to be anything else."""
    return not (key == "crates-io" or re.match(r"(?i)(https?|ssh|git)://", key))


def _replace_may_be_path(key: str) -> bool:
    """Whether a `[replace]` key may be the id of a package of a path source (`path+file:///dir#name@version`, `file:///dir#version`): it holds a
    `file:` url. `name:version`, `name@version` and the id of a registry or git source are not."""
    return re.search(r"(?i)file:|path\+", key) is not None


def _words(key: str) -> set[str]:
    """The names a `[replace]` key may spell: it is a package id (`name`, `name:version`, `name@version`, `url#version`, `url#name:version`: the name
    of an id with no other is the last segment of its url), so every word of it is read as one. A word is never part of another."""
    return {_canon(word) for word in re.split(r"[^A-Za-z0-9_-]+", key) if word}


def _entries(doc: dict, base: str, known: set[str], out: Redirects, where: str) -> None:
    """Add every `[patch.<table>]` and `[replace]` entry of a manifest or a cargo config to `out` (`base`: the directory its paths are relative to,
    the one above its `.cargo` for a config)."""
    patch, replace = doc.get("patch"), doc.get("replace")
    entries: list[tuple[set[str], object, bool]] = []  # (the names the entry spells, its value, whether it may redirect a path dependency)
    if isinstance(patch, dict) and all(isinstance(table, dict) for table in patch.values()):
        entries += [({key}, spec, _patch_may_be_path(url)) for url, table in patch.items() for key, spec in table.items()]
    elif patch is not None:
        out.unparsable = out.unparsable or f"{where} has a [patch] of a form ripple does not read"
    if isinstance(replace, dict):
        entries += [(_words(key), spec, _replace_may_be_path(key)) for key, spec in replace.items()]
    elif replace is not None:
        out.unparsable = out.unparsable or f"{where} has a [replace] of a form ripple does not read"
    for names, spec, on_path in entries:
        where_to: str | None = None
        if isinstance(spec, dict):
            package = spec.get("package")
            if isinstance(package, str):
                names = names | {package}
            elif package is not None:
                out.unparsable = out.unparsable or f"{where} has an entry whose `package` is no string"
            if isinstance(spec.get("path"), str):
                path = _join(base, spec["path"])
                where_to = path if path in known else None
        elif not isinstance(spec, str):
            out.unparsable = out.unparsable or f"{where} has an entry of a form ripple does not read"
        for name in names:
            out.names.setdefault(_canon(name), set()).add(where_to)
            if on_path:
                out.path_names.setdefault(_canon(name), set()).add(where_to)


def read_redirects(repo: Path, data: dict[str, dict], known: set[str], unreadable: set[str], configs: list[str], found: list[str]) -> Redirects:
    """Every `[patch]` and `[replace]` entry of every Cargo.toml (`data`: those above a source file; `found`: every one the walk of the repo finds,
    of a directory with no source file too) and of every cargo config of the repo (`configs`) (a `paths` entry is no table to read: it refuses
    everything), and what of it ripple could not read."""
    toml = _toml()
    out = Redirects(read=set(data), unreadable=set(unreadable))
    docs = dict(data)
    if unreadable:
        out.unread = f"{_join(min(unreadable), 'Cargo.toml')} could not be read"
    for rel in found:
        directory = posixpath.dirname(rel)
        if directory in docs or directory in unreadable:
            continue
        try:
            docs[directory] = toml.loads((repo / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            out.unread = out.unread or f"{rel} could not be read"
            out.unreadable.add(directory)
            continue
        out.read.add(directory)
    for directory, doc in sorted(docs.items()):
        _entries(doc, directory, known, out, _join(directory, "Cargo.toml"))
        package = doc.get("package")
        if isinstance(package, dict) and isinstance(package.get("name"), str):
            out.packages.setdefault(_canon(package["name"]), set()).add(directory if directory in known else None)
    for rel in configs:
        try:
            conf = toml.loads((repo / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            out.unread = out.unread or f"{rel} could not be read"
            out.path_unparsable = out.path_unparsable or f"{rel} could not be read"  # it may hold a `paths` or a patch of a path source
            continue
        base = posixpath.dirname(posixpath.dirname(rel))
        _entries(conf, base, known, out, rel)
        if "include" in conf:  # in any form: the files it names may hold any of what follows, and ripple reads none of them
            _refuse(out, f"{rel} has an `include`, whose files ripple does not read", True)
        _config_paths(conf, rel, out)
    above = above_manifests(repo)
    if above:
        out.unread = out.unread or f"{above[0]} is above the analysed directory"
    return out


def _config_paths(conf: dict, rel: str, out: Redirects) -> None:
    """The old override: `paths = ["dir"]` makes cargo load every package at or below `dir` and every path dependency of each of those, transitively,
    wherever it points (probes paths_nested_pathdep_registry, paths_nested_pathdep_over_path, paths_transitive_devdep_over_path), and a dependency
    of the name of any of them is that package, whatever its source (a `path` dependency too, probe path_dep_paths_override). Ripple follows none
    of it (three reviews in a row missed part of what cargo reaches): any entry refuses every dependency of every package, a path dependency
    too, whatever the entry holds or does not hold. An empty list is no entry (probe paths_empty); a `paths` that is no list of strings is one
    ripple cannot read, and refuses the same."""
    paths = conf.get("paths")
    if paths is None:
        return
    if not (isinstance(paths, list) and all(isinstance(p, str) for p in paths)):
        _refuse(out, f"{rel} has a `paths` of a form ripple does not read", True)
    elif paths:
        _refuse(out, f"{rel} has a `paths` entry, and cargo loads every package below it and the path dependencies of each", True)


def above_manifests(repo: Path) -> list[str]:
    """The Cargo.toml of each directory above the analysed one, as a path from it (`../Cargo.toml`). Cargo looks above a package for the
    `[workspace]` that governs it, and that workspace's `[patch]` reaches the package. Ripple does not read them (read_redirects only says they
    are there), and the cache watches whether there is one."""
    out, here, depth = [], Path(os.path.realpath(repo)), 0
    while here.parent != here:
        here, depth = here.parent, depth + 1
        if (here / "Cargo.toml").is_file():
            out.append("/".join([".."] * depth) + "/Cargo.toml")
    return out


def _dep_paths(pdir: str, data: dict[str, dict]) -> list[str]:
    """The directory (repo-relative, normalised, `../x` when it leaves the repo) of every path dependency of a package: its own tables', and a
    workspace dependency's."""
    root = _workspace_root(pdir, data)
    shared = data[root].get("workspace", {}) if root is not None and isinstance(data[root].get("workspace"), dict) else {}
    shared_deps = shared.get("dependencies", {}) if isinstance(shared.get("dependencies"), dict) else {}
    out = []
    for section in _dependency_sections(data[pdir]):
        for key, spec in (section.items() if isinstance(section, dict) else ()):
            if not isinstance(spec, dict):
                continue
            wide = shared_deps.get(key) if spec.get("workspace") and isinstance(shared_deps.get(key), dict) else None
            if wide is not None:
                if isinstance(wide.get("path"), str) and root is not None:
                    out.append(_join(root, wide["path"]))
            elif isinstance(spec.get("path"), str):
                out.append(_join(pdir, spec["path"]))
    return out


def _leaves_repo(path: str) -> bool:
    """Whether a normalised repo-relative directory is outside the analysed directory (`../x`, or an absolute path)."""
    return path == ".." or path.startswith("../") or posixpath.isabs(path)


def outside_manifests(repo: Path, sources: list[Path]) -> list[str]:
    """The Cargo.toml of every path dependency that leaves the analysed directory, as a path from it (`../lib/Cargo.toml`): the one file of
    such a package that ripple reads (_outside_lib), so the cache fingerprints it."""
    data: dict[str, dict] = {}
    for rel in manifests(repo, sources):
        try:
            data[posixpath.dirname(rel)] = _toml().loads((repo / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    out = set()
    for pdir in data:
        try:
            out |= {_join(path, "Cargo.toml") for path in _dep_paths(pdir, data) if _leaves_repo(path)}
        except (AttributeError, TypeError, ValueError, KeyError):
            continue  # a manifest cargo would reject: load_workspace says so
    return sorted(out)


def _outside_lib(repo: Path, path: str) -> str | None:
    """The library name of the package at a directory outside the analysed directory, from its Cargo.toml alone (the package's name, or the
    `[lib]` table's: no source file of it is read), or None when that file cannot be read or does not say."""
    try:
        manifest = _toml().loads((repo / path / "Cargo.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    package, lib = manifest.get("package"), manifest.get("lib", {})
    if not isinstance(package, dict) or not isinstance(package.get("name"), str) or not isinstance(lib, dict):
        return None
    name = lib.get("name", package["name"])
    return name.replace("-", "_") if isinstance(name, str) else None


# ---- whether a dependency is the published crate
#
# ripple trusts three attribute macros (rs_indexer.TRUSTED_ATTRS) and seven derives (rs_indexer.TRUSTED_DERIVES) only where
# the repo shows that the crate that defines each is the one on crates.io. Each is the macro of a crate (`tokio::main` is
# tokio's, written by tokio-macros; `Serialize` is serde's, written by serde_derive) and the proof covers both: the
# dependency as the package's manifests write it, what a workspace, a lock or a cargo config file may replace it with, and
# that no package of the repo is called by the name of either. What lives outside the repo (a config file above it,
# RUSTFLAGS, a wrapper, a registry mirror that serves other code) cannot be seen and is the one assumption the README states.

# crate -> the crate that defines its macros. A derive is named through the crate itself (`serde`, never `serde_derive`: see
# rs_indexer.TRUSTED_DERIVES), whose own build resolves the macro crate.
TRUSTED_CRATES = {
    "tokio": "tokio-macros", "serial_test": "serial_test_derive",
    "serde": "serde_derive", "clap": "clap_derive", "thiserror": "thiserror-impl",
}  # fmt: skip
# The crates of TRUSTED_CRATES a library of the repo called like the crate or its macro crate refuses. rustc links a library of the repo
# where the real crate was meant when that is a proc-macro dylib and the repo's is an rlib, which it prefers, so a derive named through a macro
# crate (`serde_derive::Serialize`) could be captured: that is not trusted at all now. Named through the crate itself the derive cannot be
# (probed with local crates and with the real ones: a second rlib of the crate's name is E0464, a proc-macro library of it loses to the real
# rlib, a library of the macro crate's name is not what the crate's own build links), so this refusal is a second guard, kept because it only
# costs a repo with such a library its recall. The attribute macros are not checked: the same probes.
LIBRARY_CHECKED = frozenset({"serde", "clap", "thiserror"})
PLAIN_KEYS = frozenset({"version", "features", "default-features", "default_features", "optional"})
WORKSPACE_KEYS = frozenset({"workspace", "features", "optional", "default-features", "default_features"})
DEPENDENCY_TABLES = ("dependencies", "dev-dependencies", "build-dependencies", "dev_dependencies", "build_dependencies")
CRATES_IO = ("registry+https://github.com/rust-lang/crates.io-index", "sparse+https://index.crates.io/")
# What a cargo config file may set and still leave every crate name, and `rustc`, meaning what the manifests say. Cargo reads
# `.cargo/config*` from the directory it is run in upward, which may be anywhere in the repo (`cargo build --manifest-path
# ../app/Cargo.toml` run from `docs/`), so every one `cargo_configs` finds is read and each must hold nothing but these keys. Anything
# else refuses the whole repo: `paths`, `source`, `patch`, `include`, `registries`, `registry`, `alias` (a `--config` in its
# expansion), `profile`, `build.rustc*`, `resolver.lockfile-path`, a `[target.<t>.<links>]` build-script override, and any key
# not listed, so a setting cargo adds later refuses until it is read.
CONFIG_KEYS = {
    "build": {"jobs", "target", "target-dir", "build-dir", "artifact-dir", "incremental", "dep-info-basedir", "pipelining", "warnings", "rustflags"},
    "target": None,  # each `[target.<triple or cfg>]`, see TARGET_KEYS
    "term": None,  # how cargo prints
    "cache": None,
    "future-incompat-report": {"frequency"},
    "doc": {"browser"},
    "net": {"retry", "git-fetch-with-cli", "offline"},
    "http": {"debug", "check-revoke", "ssl-version", "low-speed-limit", "timeout", "multiplexing", "user-agent"},  # not `proxy` or `cainfo`
    "install": {"root"},
    "resolver": {"incompatible-rust-versions", "feature-unification"},  # not `lockfile-path`
    "env": None,
}
# The set for the other question, whether a config can make `std`, `core` or `alloc` another crate or `rustc` another compiler. Those keys
# of the trust proof's refusal that cannot: an `[alias]` (cargo never lets one shadow `build`, `test` or `check`: built, "user-defined alias
# `test` is ignored"), `[source]` and `[registries]` and `registry` (only where a registry or a git dependency comes from, which the
# README assumes is no crate called `std`), and a `[profile]` unless it sets rustflags. Still refused: `patch` (Rule P reads it for which
# dependency may be redirected, and a library a patch may supply may be called `std`), `paths`, `include`, `[unstable]`, the forbidden `env` entries, rustflags beyond codegen, lints and `--cfg`,
# `build.rustc*`, and any key not listed.
STD_CONFIG_KEYS = {**CONFIG_KEYS, "alias": None, "source": None, "registries": None, "registry": None, "profile": None}
TARGET_KEYS = frozenset({"rustflags", "linker", "runner", "ar"})
# `[env]` entries cargo hands to rustc and to build scripts: not the ones that choose a compiler, a wrapper, flags or a search path
ENV_FORBIDDEN = ("RUSTC", "RUSTFLAGS", "RUSTDOC", "RUSTUP", "CARGO", "PATH", "LD_", "DYLD_")
# `-C` options that set how code is generated and linked, never which crates or which source are read
SAFE_CODEGEN = frozenset(
    "target-feature target-cpu link-arg link-args link-self-contained opt-level debuginfo lto codegen-units panic relocation-model "
    "strip embed-bitcode split-debuginfo force-frame-pointers overflow-checks debug-assertions prefer-dynamic rpath incremental "
    "linker linker-flavor linker-plugin-lto code-model control-flow-guard force-unwind-tables no-redzone".split()
)
PRUNE_CONFIG_WALK = frozenset({".git", "node_modules", "target", "__pycache__"})


def _norm(name: str) -> str:
    return name.replace("-", "_")


def _plain(spec, keys: frozenset = PLAIN_KEYS) -> bool:
    """A dependency spec that names the registry package of its key and nothing else: a version, or a table of a version
    and features."""
    return isinstance(spec, str) or isinstance(spec, dict) and set(spec) <= keys and isinstance(spec.get("version", ""), str)


def _dependency_sections(manifest: dict) -> list:
    """Every dependency table of a manifest: its own, and those under each `[target.<cfg>]`. Ripple does not tell which one
    applies, so each must be plain."""
    targets = manifest.get("target", {})
    scopes = (manifest, *(t for t in (targets.values() if isinstance(targets, dict) else ()) if isinstance(t, dict)))
    return [scope.get(key, {}) for scope in scopes for key in DEPENDENCY_TABLES]


def _dependency_problem(sections: list, shared_deps: dict, crate: str, macro_crate: str) -> str | None:
    found = False
    why = None
    for section in sections:
        if not isinstance(section, dict):
            continue
        for key, spec in section.items():
            if _norm(key) != crate:
                if isinstance(spec, dict) and isinstance(spec.get("package"), str) and _norm(spec["package"]) in (crate, _norm(macro_crate)):
                    why = why or f"the dependency {key} is the package {spec['package']} renamed"
                continue
            found = True
            if key != crate:
                why = why or f"the dependency is written {key}, not {crate}"
            elif isinstance(spec, dict) and spec.get("workspace") is True:
                if not _plain(spec, WORKSPACE_KEYS):
                    why = why or f"the dependency {key} inherits from the workspace with other keys"
                elif not _plain(shared_deps.get(key)):
                    why = why or f"the workspace dependency {key} is not a plain registry version"
            elif not _plain(spec):
                why = why or f"the dependency {key} is not a plain registry version (path, git, registry, package or another key)"
    return why if found else why or f"the package does not depend on {crate}"


def _patch_entries(manifest: dict) -> str | None:
    """Why a manifest carries a `[replace]` or a `[patch.<registry>]` entry, which makes some crate another one (in any spelling of
    its key: a name, `name:version`, `name@version`, a url). None when it carries none. It is not asked which crate: a patch of
    `quote`, `syn` or `proc-macro2` reaches the macro crates too, and one that names no crate ripple knows may name it by a
    `package` key."""
    replace, patch = manifest.get("replace"), manifest.get("patch")
    if replace:
        return "a [replace] entry"
    if patch is not None and not isinstance(patch, dict):
        return "a [patch] of a form ripple does not read"
    for registry, entries in (patch or {}).items():
        if not isinstance(entries, dict):
            return f"a [patch.{registry}] of a form ripple does not read"
        if entries:
            return f"a [patch.{registry}] entry"
    return None


def _flags_problem(flags) -> str | None:
    """Why `rustflags` may change what a crate is: a flag that is not a codegen option, a lint level or a `--cfg` (`--extern`,
    `-L`, `--sysroot`, `-Z..`, `--crate-attr`, `--remap-path-prefix`, a path of a linker plugin...). A string is split on spaces, as cargo does."""
    tokens = flags.split() if isinstance(flags, str) else flags
    if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens):
        return "rustflags of a form ripple does not read"
    wanted = None  # the flag whose value is next
    for token in tokens:
        if wanted == "-C":
            if token.split("=")[0] not in SAFE_CODEGEN:
                return f"the rustflag -C {token}"
        elif wanted is not None:
            pass  # a lint name, or what a --cfg sets
        elif token in ("-C", "-D", "-W", "-A", "-F", "--cfg"):
            wanted = token
            continue
        elif token.startswith("-C") and token[2:].split("=")[0] in SAFE_CODEGEN:
            pass
        elif token[:2] in ("-D", "-W", "-A", "-F") and len(token) > 2 and not token.startswith("--"):
            pass
        elif token.startswith("--cfg=") and len(token) > 6:
            pass
        else:
            return f"the rustflag {token}"
        wanted = None
    return f"rustflags ends in {wanted}" if wanted else None


def _profile_flags(value) -> bool:
    """Whether a `[profile]` value sets `rustflags` anywhere in it (`profile.dev`, `profile.dev.package."*"`, `profile.dev.build-override`)."""
    if isinstance(value, dict):
        return any(key == "rustflags" or _profile_flags(sub) for key, sub in value.items())
    return False


def _config_table_problem(conf: dict, keys: dict = CONFIG_KEYS) -> str | None:
    for key, value in conf.items():
        if key not in keys:
            return f"the key `{key}`"
        allowed = keys[key]
        if key == "profile":  # only in STD_CONFIG_KEYS
            if not isinstance(value, dict):
                return "`profile` of a form ripple does not read"
            if _profile_flags(value):
                return "`profile` sets rustflags"
        elif key == "env":
            if not isinstance(value, dict):
                return "`env` of a form ripple does not read"
            for name in value:
                if str(name).upper().startswith(ENV_FORBIDDEN):
                    return f"`env` sets {name}"
        elif key == "target":
            if not isinstance(value, dict) or not all(isinstance(t, dict) for t in value.values()):
                return "`target` of a form ripple does not read"
            for triple, table in value.items():
                for sub, setting in table.items():
                    if sub not in TARGET_KEYS:
                        return f"the key `target.{triple}.{sub}` (a build script override, or a setting ripple does not know)"
                    if sub == "rustflags" and (why := _flags_problem(setting)):
                        return f"target.{triple}: {why}"
        elif allowed is not None:
            if not isinstance(value, dict):
                return f"`{key}` of a form ripple does not read"
            for sub, setting in value.items():
                if sub not in allowed:
                    return f"the key `{key}.{sub}`"
                if key == "build" and sub == "rustflags" and (why := _flags_problem(setting)):
                    return f"build: {why}"
    return None


def cargo_configs(repo: Path) -> list[str]:
    """Every cargo config file cargo could read for a package of the repo (a file called `config` or `config.toml` in a directory called
    `.cargo`), repo-relative and sorted: the first half of `_walk`."""
    return _walk(repo)[0]


def _walk(repo: Path) -> tuple[list[str], list[str]]:
    """(every cargo config file, every Cargo.toml) of the repo, repo-relative and sorted, wherever they are. The configs are the files called
    `config` or `config.toml` in a directory called `.cargo`, with the names matched in any case (cargo on a case-insensitive file system
    reads `.Cargo/Config.toml`; reading it everywhere only refuses more). The manifests are every Cargo.toml, in a directory with a source
    file below it or not (a root with no source of its own may hold the `[patch]` of a member elsewhere). The walk follows a link to a
    directory that is inside the repo (each directory once, by its real path, so a loop ends) and reads a `.cargo` that is a link wherever
    it points; it does not enter a link that leaves the repo, which cargo reads for no package of the repo. It skips `.git` and
    `node_modules`, and a `target` directory with a Cargo.toml beside it, which is where the source walk reads no file either
    (rs_indexer.reads_as_source): one with none beside it is a directory like any other, and a package may sit in it."""
    root = os.path.realpath(repo)
    found: list[str] = []
    manifest_files: list[str] = []
    visited = {root}
    stack = [(os.fspath(repo), "", root)]  # (path as walked, repo-relative path, real path)
    while stack:
        here, rel, real = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        manifest = any(e.name == "Cargo.toml" and e.is_file() for e in entries)
        if manifest:
            manifest_files.append(f"{rel}/Cargo.toml" if rel else "Cargo.toml")
        for entry in sorted(entries, key=lambda e: (e.is_symlink(), e.name)):  # a real directory is walked before a link to it
            try:
                if not entry.is_dir():  # a link is followed here: a link to a file or a dangling one is no directory
                    continue
            except OSError:
                continue
            name, path = entry.name, f"{rel}/{entry.name}" if rel else entry.name
            if name.lower() == ".cargo":
                found += _config_names(entry.path, path)
            if name in PRUNE_CONFIG_WALK and (name != "target" or manifest):
                continue
            below = os.path.realpath(entry.path) if entry.is_symlink() else f"{real}/{name}"
            if below in visited or (entry.is_symlink() and not _within(below, root)):
                continue
            visited.add(below)
            stack.append((entry.path, path, below))
    return sorted(set(found)), sorted(set(manifest_files))


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _config_names(directory: str, rel: str) -> list[str]:
    """The config files in one `.cargo` directory, as the names are written there."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [f"{rel}/{n}" for n in names if n.lower() in ("config", "config.toml") and os.path.isfile(os.path.join(directory, n))]


def _config_problems(repo: Path, configs: list[str] | None = None) -> tuple[str | None, str | None]:
    """Why a cargo config file `cargo_configs` finds (`configs`, when the caller has walked) may change what a crate or `rustc` is, read once
    for both questions: (for the trust proof, for what `std` names). A file that cannot be read, or one that sets something that is not in
    CONFIG_KEYS (the trust proof) or STD_CONFIG_KEYS (std), or a rustflag or an env entry that is not allowed, is the reason; None when no
    file has one."""
    toml = _toml()
    trust = std = None
    for rel in cargo_configs(repo) if configs is None else configs:
        try:
            conf = toml.loads((repo / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            trust = trust or f"{rel} could not be read"
            std = std or f"{rel} could not be read"
            continue
        if trust is None and (why := _config_table_problem(conf)) is not None:
            trust = f"{rel} sets {why}"
        if std is None and (why := _config_table_problem(conf, STD_CONFIG_KEYS)) is not None:
            std = f"{rel} sets {why}"
    return trust, std


def _config_problem(repo: Path) -> str | None:
    """Why a cargo config of the repo may change what a crate is, for the trust proof."""
    return _config_problems(repo)[0]


def _lock_packages(repo: Path, base: str):
    """The [[package]] tables of the Cargo.lock beside a workspace root: a list, None when there is no lock, "bad" when it
    cannot be read."""
    path = repo / base / "Cargo.lock"
    if not path.is_file():
        return None
    try:
        packages = _toml().loads(path.read_text(encoding="utf-8")).get("package", [])
    except (OSError, ValueError, UnicodeDecodeError):
        return "bad"
    return [p for p in packages if isinstance(p, dict)] if isinstance(packages, list) else "bad"


def _lock_problem(locked, crate: str, macro_crate: str) -> str | None:
    """With a lock, each of the crate and its macro crate must be in it, and from crates.io. The lock shows what a `[patch]`
    from outside the repo made of them when it was written, which no manifest does. Without one the manifests alone prove it."""
    if locked is None:
        return None
    if locked == "bad":
        return "Cargo.lock could not be read"
    entries = [p for p in locked if _norm(str(p.get("name", ""))) in (crate, _norm(macro_crate))]
    if {_norm(str(p["name"])) for p in entries} != {crate, _norm(macro_crate)}:
        return f"Cargo.lock does not list {crate} and {macro_crate}"
    for p in entries:
        if not str(p.get("source", "")).startswith(CRATES_IO):
            return f"Cargo.lock takes {p['name']} from {p.get('source', 'the repo (it has no source)')}, not crates.io"
    return None


def _excluded(root_manifest: dict, root: str, pdir: str) -> bool:
    """Whether the workspace the manifest at `root` is excludes the package at `pdir`, as ripple can tell: an `exclude` entry that is
    the package's directory or one above it, or one with a glob in it (cargo then builds the package as a workspace of its own, with
    its own Cargo.lock)."""
    workspace = root_manifest.get("workspace")
    exclude = workspace.get("exclude", []) if isinstance(workspace, dict) else []
    if not isinstance(exclude, list):
        return True
    for entry in exclude:
        if not isinstance(entry, str) or any(c in entry for c in "*?["):
            return True
        path = _join(root, entry)
        if pdir == path or pdir.startswith(path + "/") or not path:
            return True
    return False


def _scope_problem(pdir: str, data: dict[str, dict], unreadable: set[str]) -> str | None:
    """Why what governs the package as a whole (its manifest, the manifests above it and the workspace that holds it) is not all read
    and free of replacements: a Cargo.toml at or above it that could not be read, a `package.workspace` that names a manifest ripple
    has not read, an `exclude` that makes the package its own workspace (so the lock above it is not its lock), a `[replace]` or
    `[patch]` entry in any of them."""
    here, chain = pdir, []
    while True:
        chain.append(here)
        if not here:
            break
        here = posixpath.dirname(here)
    for d in chain:
        if d in unreadable:
            return f"{_join(d, 'Cargo.toml')} could not be read"
    manifest = data[pdir]
    named = manifest.get("package", {}).get("workspace")
    if named is not None:
        root = _join(pdir, named) if isinstance(named, str) else None
        if root is None or root not in data or "workspace" not in data[root]:
            return f"package.workspace names {named!r}, a manifest with a workspace that ripple has not read"
    root = _workspace_root(pdir, data)
    if root is not None and root != pdir and _excluded(data[root], root, pdir):
        return f"{_join(root, 'Cargo.toml')} excludes this package, which cargo then builds as a workspace of its own"
    for d in dict.fromkeys([pdir, *chain, *([root] if root is not None else [])]):
        if d in data and (why := _patch_entries(data[d])) is not None:
            return f"{_join(d, 'Cargo.toml')} has {why}"
    return None


def _library_names(ws: Workspace, data: dict[str, dict], unreadable: set[str], extras: dict[str, dict] | None = None) -> tuple[set[str], str | None]:
    """The names the libraries of the repo's packages are linked under (a package called `shim` whose `[lib] name` is `serde_derive` is
    `serde_derive` to rustc, which links it where the real proc-macro crate was meant), hyphens as underscores; and why that set may not
    be all of them: a manifest that could not be read, or a package whose fields have the wrong type, may hold a library of any name,
    and so may a `[lib]` table whose name is no string. The packages of `extras` (path dependencies the walk gave no source file to,
    `_extra_manifests`) are in the set under their `[lib] name` and under their package's name, since either may be the library's."""
    libs: set[str] = set()
    why = None
    for pdir, manifest in (extras or {}).items():
        if not _named(manifest):
            continue
        lib = manifest.get("lib")
        if isinstance(lib, dict) and "name" in lib and not isinstance(lib["name"], str):
            why = why or f"{_join(pdir, 'Cargo.toml')} has a [lib] whose name ripple cannot read"
        elif isinstance(lib, dict) and "name" in lib:
            libs.add(_norm(lib["name"]))
        libs.add(_norm(manifest["package"]["name"]))
    packaged = {d for d, p in ws.packages.items()}
    for pdir, manifest in data.items():
        if pdir in packaged:
            package = ws.packages[pdir]
            lib = manifest.get("lib")
            if isinstance(lib, dict) and "name" in lib and not isinstance(lib["name"], str):
                why = why or f"{_join(pdir, 'Cargo.toml')} has a [lib] whose name ripple cannot read"
            if package.lib_name:
                libs.add(_norm(package.lib_name))
        elif isinstance(manifest.get("package"), dict):
            why = why or f"{_join(pdir, 'Cargo.toml')} is a package ripple cannot read"
    for pdir in sorted(unreadable):
        why = why or f"{_join(pdir, 'Cargo.toml')} cannot be read, and may hold a library of any name"
    return libs, why


def _lock_base(pdir: str, data: dict[str, dict]) -> str:
    """The directory whose Cargo.lock governs a package: its workspace's root, else its own."""
    root = _workspace_root(pdir, data)
    return root if root is not None else pdir


# The registry packages a derive of rs_indexer.TRUSTED_DERIVES is built through (rs_indexer.LOCK_PROVIDERS): the requirements the manifests
# write for them are what a Cargo.lock must still satisfy.
REQUIRED_CRATES = frozenset({"serde", "serde_derive", "clap", "clap_derive", "thiserror", "thiserror_impl"})


def _named(manifest: dict) -> bool:
    """Whether a parsed manifest is a package that has a name."""
    return isinstance(manifest.get("package"), dict) and isinstance(manifest["package"].get("name"), str)


def _path_dependencies(pdir: str, manifest: dict, data) -> list[str | None]:
    """The directory of each path dependency of the package at `pdir`, in every dependency table (also `[target.<cfg>.dependencies]`),
    repo-relative and normalised (so `..` and an absolute path are written as such, which `_extra_manifests` and `_requirements` find
    outside the repo). A dependency that inherits (`lib.workspace = true`) takes the path the workspace root writes, from the root's
    directory. None stands for a path that is no string, or an inherited one the root gives no usable path for: the dependency is
    a package ripple cannot find."""
    root = _workspace_root(pdir, data)
    shared = (data[root] if root is not None else manifest).get("workspace", {})
    shared_deps = shared.get("dependencies", {}) if isinstance(shared, dict) and isinstance(shared.get("dependencies"), dict) else {}
    found: list[str | None] = []
    for section in _dependency_sections(manifest):
        if not isinstance(section, dict):
            continue
        for key, spec in section.items():
            if not isinstance(spec, dict):
                continue
            if spec.get("workspace") is True:
                wide = shared_deps.get(key)
                if isinstance(wide, dict) and "path" in wide:
                    found.append(_join(root, wide["path"]) if isinstance(wide["path"], str) and root is not None else None)
            elif "path" in spec:
                found.append(_join(pdir, spec["path"]) if isinstance(spec["path"], str) else None)
    return found


def _outside(repo: Path, target: str) -> bool:
    """Whether a path dependency's directory (as `_path_dependencies` writes it) is not inside the analysed directory: written above it
    or absolute, or a link that leaves it."""
    if posixpath.isabs(target) or target == ".." or target.startswith("../"):
        return True
    return not _within(os.path.realpath(repo / target), os.path.realpath(repo))


def _member_dirs(repo: Path, root: str, workspace: dict) -> list[str | None]:
    """The directories the `members` of a `[workspace]` table at `root` name, repo-relative and normalised (a path that leaves the repo
    stays written so: `_outside`). A glob entry is expanded the way cargo does for one level at a time (`*`, `?` and `[..]` in a segment,
    hidden directories included), keeping the directories that hold a Cargo.toml. None stands for what ripple cannot tell: a `members` that is
    no list of strings, an entry that is no string, or one with a `**` segment."""
    members = workspace.get("members", [])
    if not isinstance(members, list):
        return [None]
    out: list[str | None] = []
    for entry in members:
        if not isinstance(entry, str) or "\\" in entry:
            out.append(None)
        elif not any(c in entry for c in "*?["):
            out.append(_join(root, entry))
        elif "**" in entry:
            out.append(None)
        else:
            dirs = [root]
            for segment in (s for s in entry.split("/") if s not in ("", ".")):
                nxt: list[str] = []
                for d in dirs:
                    if not any(c in segment for c in "*?["):
                        nxt.append(_join(d, segment))
                        continue
                    try:
                        names = sorted(e.name for e in os.scandir(repo / d) if e.is_dir())
                    except OSError:
                        continue
                    nxt += [_join(d, n) for n in names if fnmatch.fnmatchcase(n, segment)]
                dirs = nxt
            out += [d for d in dirs if _outside(repo, d) or (repo / d / "Cargo.toml").is_file()]
    return out


def _extra_manifests(repo: Path, data: dict[str, dict], unreadable: set[str]) -> tuple[dict[str, dict], list[str], dict[str, list[str | None]]]:
    """The manifests of the packages the lock covers that the walk gave no source file to (a package whose `lib.rs` is in another directory,
    one in a hidden directory or in `build/` at the root), found from the manifests the walk gave by following each package's path
    dependencies, transitively, and the `members` of each `[workspace]`: (directory -> parsed manifest, the repo-relative manifest paths
    the cache must fingerprint, each workspace root -> its member directories as `_member_dirs` reads them). The requirements of the
    lock are read from them (`_requirements`). Only inside the analysed directory: a package above it is never read, so it counts as
    unreadable there. A manifest that exists and does not parse is in the second list and not the first."""
    extras: dict[str, dict] = {}
    files: list[str] = []
    seen = set(data) | set(unreadable)
    merged = ChainMap(extras, data)
    toml = _toml()
    queue: list[str] = []

    def visit(target: str | None) -> None:
        if target is None or target in seen or _outside(repo, target):
            return
        seen.add(target)
        path = repo / target / "Cargo.toml"
        if not path.is_file():
            return
        files.append(_join(target, "Cargo.toml"))
        try:
            extras[target] = toml.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return
        queue.append(target)

    members = {root: _member_dirs(repo, root, data[root]["workspace"]) for root in sorted(data) if isinstance(data[root].get("workspace"), dict)}
    for dirs in members.values():
        for member in dirs:
            visit(member)
    queue += [pdir for pdir in sorted(data) if isinstance(data[pdir].get("package"), dict)]
    while queue:
        pdir = queue.pop(0)
        try:
            targets = _path_dependencies(pdir, merged[pdir], merged)
        except (AttributeError, TypeError, ValueError, KeyError):
            continue  # a manifest cargo would reject: _requirements counts it as unreadable
        for target in targets:
            visit(target)
    return extras, files, members


def _requirements(
    data: dict[str, dict], base: str, extras: dict[str, dict] | None = None, packages: set[str] | None = None, member_dirs=()
) -> dict[str, tuple[str | None, ...]]:
    """The version requirement written for each crate of REQUIRED_CRATES by every package the Cargo.lock at `base` covers: the packages of
    the workspace whose lock it is (those the walk read, and the `members` of its `[workspace]` the walk gave no source file to:
    `member_dirs`, `_member_dirs`), and the transitive closure of their path dependencies (cargo resolves one lock over every package the
    root reaches, members or not, so a path dependency that pins `serde = "=1.0.180"` decides what builds). Each is read in every
    dependency table (also `[target.<cfg>.dependencies]`), by the package it names (a renamed dependency counts under its real name).
    One that is inherited (`serde.workspace = true`) is the root's. None stands for a requirement ripple cannot read as a version
    string: a path or git dependency without one, an inherited one the root does not write, a `package` that is no string.

    A package ripple cannot read is a requirement it cannot see, so it is None for every crate of REQUIRED_CRATES (once): a path dependency
    or a member that is not in `data` (the walk read it) or `extras` (`_extra_manifests`: a manifest inside the analysed directory the walk
    gave no source file to), a manifest that is no package or whose fields have the wrong type, a path that is no string. `packages` are
    the directories of the packages `_package` could read."""
    out: dict[str, list[str | None]] = {}
    merged = ChainMap(extras or {}, data)
    seeds = [pdir for pdir, manifest in data.items() if isinstance(manifest.get("package"), dict) and _lock_base(pdir, data) == base]
    queue = list(seeds)
    done: set[str] = set()
    blind = False

    def reach(target: str | None) -> None:
        nonlocal blind
        if target is None or target not in merged or not isinstance(merged[target].get("package"), dict) or (packages is not None and target in data and target not in packages):
            blind = True
        elif target not in done:
            queue.append(target)

    for member in member_dirs:  # the workspace's own members: read like a path dependency
        reach(member)
    for pdir in queue:  # (the list grows as packages are found)
        if pdir in done:
            continue
        done.add(pdir)
        try:
            found, targets = _package_requirements(pdir, merged[pdir], merged)
        except (AttributeError, TypeError, ValueError, KeyError):
            if pdir in seeds:
                raise
            blind = True  # a manifest cargo would reject
            continue
        for crate, written in found:
            out.setdefault(crate, []).append(written)
        for target in targets:
            reach(target)
    if blind:
        for crate in REQUIRED_CRATES:
            out.setdefault(crate, []).append(None)
    return {crate: tuple(found) for crate, found in out.items()}


def _package_requirements(pdir: str, manifest: dict, data) -> tuple[list[tuple[str, str | None]], list[str | None]]:
    """([(crate of REQUIRED_CRATES, the requirement the package writes for it, None where it cannot be read)], its path dependencies)."""
    found: list[tuple[str, str | None]] = []
    root = _workspace_root(pdir, data)
    shared = (data[root] if root is not None else manifest).get("workspace", {})
    shared_deps = shared.get("dependencies", {}) if isinstance(shared, dict) and isinstance(shared.get("dependencies"), dict) else {}
    for section in _dependency_sections(manifest):
        if not isinstance(section, dict):
            continue
        for key, spec in section.items():
            real, written = key, None
            if isinstance(spec, str):
                written = spec
            elif isinstance(spec, dict):
                real = spec.get("package", key)
                if spec.get("workspace") is True:
                    wide = shared_deps.get(key)
                    if isinstance(wide, dict):
                        real = wide.get("package", key)
                    written = wide if isinstance(wide, str) else wide.get("version") if isinstance(wide, dict) else None
                else:
                    written = spec.get("version")
            if not isinstance(real, str):  # a package that is no string may be any crate
                found += [(crate, None) for crate in REQUIRED_CRATES]
            elif _norm(real) in REQUIRED_CRATES:
                found.append((_norm(real), written if isinstance(written, str) else None))
    return found, _path_dependencies(pdir, manifest, data)


def _lock_versions(locked) -> dict[str, tuple[str, ...]] | None:
    """What a lock (as _lock_packages reads it) says is built: each package name, hyphens as underscores, with every version the
    lock lists for it (a lock may hold several of one crate). None when there is no lock or it cannot be read."""
    if not isinstance(locked, list):
        return None
    out: dict[str, list[str]] = {}
    for p in locked:
        out.setdefault(_norm(str(p.get("name", ""))), []).append(str(p.get("version", "")))
    return {name: tuple(versions) for name, versions in out.items()}


def _trust(
    repo: Path, pdir: str, data: dict[str, dict], names: set[str], unreadable: set[str], config_why: str | None, locks: dict[str, object] | None = None,
    libs: set[str] = frozenset(), libs_why: str | None = None,
) -> dict[str, str | None]:
    """For each crate of TRUSTED_CRATES, None when the repo proves that this package's use of it is the published crate (from
    crates.io, unrenamed, unpatched, and its macro crate too), else why it does not. `locks` holds the locks already read
    (workspace root -> _lock_packages), and gets this package's if it was not. `libs` are the names the repo's libraries are linked
    under (_library_names): for the crates of LIBRARY_CHECKED one called like the crate or its macro crate refuses, and so does a
    library whose name could not be read (`libs_why`)."""
    manifest = data[pdir]
    root = _workspace_root(pdir, data)
    top = data[root] if root is not None else manifest
    base = root if root is not None else pdir
    shared = top.get("workspace", {}) if isinstance(top.get("workspace"), dict) else {}
    shared_deps = shared.get("dependencies", {}) if isinstance(shared.get("dependencies"), dict) else {}
    sections = _dependency_sections(manifest)
    scope_why = _scope_problem(pdir, data, unreadable)
    if locks is None:
        locks = {}
    if base not in locks:
        locks[base] = _lock_packages(repo, base)
    locked = locks[base]
    out: dict[str, str | None] = {}
    for crate, macro_crate in TRUSTED_CRATES.items():
        why = _dependency_problem(sections, shared_deps, crate, macro_crate)
        if not why and names & {crate, _norm(macro_crate)}:
            why = f"a package of the repo is called {crate} or {macro_crate}"
        if not why and crate in LIBRARY_CHECKED:
            if libs_why:
                why = f"ripple cannot read every library of the repo: {libs_why}"
            elif libs & {crate, _norm(macro_crate)}:
                why = f"a package of the repo has a library called {crate} or {macro_crate}"
        out[crate] = why or scope_why or config_why or _lock_problem(locked, crate, macro_crate)
    return out


def _targets(repo: Path, package: Package) -> list[Crate]:
    """The crates a package builds, found as cargo finds them (a path or a name in the manifest, else a
    file in the conventional place)."""
    manifest, meta, base = package.manifest, package.manifest["package"], package.dir

    def listed(key: str) -> list:
        """The tables of an array in the manifest (`[[bin]]`); anything else is no table."""
        found = manifest.get(key, [])
        return [e for e in found if isinstance(e, dict)] if isinstance(found, list) else []

    def written(entry: dict, key: str, default: str | None = None) -> str | None:
        value = entry.get(key, default)
        return value if isinstance(value, str) else default

    def exists(rel: str) -> bool:
        return (repo / rel).is_file()

    def edition(entry: dict) -> str | None:
        """The edition a target table sets, None when it sets none; one that is no string is one nothing reads ("")."""
        if "edition" not in entry:
            return None
        return entry["edition"] if isinstance(entry["edition"], str) else ""

    out: list[Crate] = []
    src_lib = _join(base, "src/lib.rs")
    if package.lib_name is None and exists(src_lib):
        package.lib_name = package.name.replace("-", "_")
    if package.lib_name:
        lib = manifest.get("lib", {}) if isinstance(manifest.get("lib"), dict) else {}
        out.append(Crate(package.lib_name, "lib", _join(base, written(lib, "path", "src/lib.rs")), package, edition(lib)))
    seen: set[str] = set()
    for entry in listed("bin"):
        name = written(entry, "name", package.name)
        path = written(entry, "path") or next(
            (c for c in (f"src/bin/{name}.rs", f"src/bin/{name}/main.rs", "src/main.rs") if exists(_join(base, c))), None
        )
        if path:
            root = _join(base, path)
            out.append(Crate(name.replace("-", "_"), "bin", root, package, edition(entry)))
            seen.add(root)
    if meta.get("autobins", True):
        main = _join(base, "src/main.rs")
        if exists(main) and main not in seen:
            out.append(Crate(package.name.replace("-", "_"), "bin", main, package))
        out += _discovered(repo, package, "src/bin", "bin", seen)
    for kind, directory, key, auto in (
        ("test", "tests", "test", "autotests"),
        ("example", "examples", "example", "autoexamples"),
        ("bench", "benches", "bench", "autobenches"),
    ):
        seen_roots: set[str] = set()
        for entry in listed(key):
            entry_name = written(entry, "name")
            if entry_name is None:
                continue
            path = written(entry, "path") or (
                f"{directory}/{entry_name}.rs"
                if exists(_join(base, f"{directory}/{entry_name}.rs"))
                else f"{directory}/{entry_name}/main.rs"
            )
            root = _join(base, path)
            seen_roots.add(root)
            out.append(Crate(entry_name.replace("-", "_"), kind, root, package, edition(entry)))
        if meta.get(auto, True):
            out += _discovered(repo, package, directory, kind, seen_roots)
    build = meta.get("build", None)
    if build is not False:
        root = _join(base, build if isinstance(build, str) else "build.rs")
        if exists(root):
            out.append(Crate("build_script_build", "build", root, package))
    return out


def _discovered(repo: Path, package: Package, directory: str, kind: str, seen: set[str]) -> list[Crate]:
    """The crates of a directory cargo scans: each `name.rs` and each `name/main.rs`."""
    here = repo / package.dir / directory
    if not here.is_dir():
        return []
    found = []
    for entry in sorted(here.iterdir()):
        candidate = entry if entry.suffix == SUFFIX else entry / "main.rs"
        if candidate.is_file():
            rel = _join(package.dir, directory, candidate.relative_to(here).as_posix())
            if rel not in seen:
                found.append(Crate(entry.stem.replace("-", "_"), kind, rel, package))
    return found


@dataclass
class Module:
    crate: Crate
    path: tuple[str, ...]  # () is the crate root
    file: str
    node: object  # the source_file or declaration_list whose items the module holds
    src: bytes
    parent: Module | None
    inline: tuple[str, ...] = ()  # the inline modules between the file's own module and this one
    cfg: bool = False  # declared under a #[cfg]: which declaration is built depends on the build
    vis: str = ""  # the visibility modifier its `mod` declaration is written with ("pub(crate)"), "" when it has none
    decl: object = None  # the `mod` declaration (a mod_item of the parent's file), None for a crate root


@dataclass
class Tree:
    modules: list[Module] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


Parse = Callable[[str], "tuple[bytes, object] | None"]


def _attributes(item) -> list:
    """The attribute_item nodes written directly before an item."""
    found = []
    previous = item.prev_named_sibling
    while previous is not None and previous.type in ("attribute_item", "line_comment", "block_comment"):
        if previous.type == "attribute_item":
            found.append(previous)
        previous = previous.prev_named_sibling
    return found


def _read(src: bytes, node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _name(src: bytes, node) -> str:
    """A name as the compiler sees it: `r#try` is `try` (and `mod r#try;` is the file try.rs)."""
    written = _read(src, node)
    return written[2:] if written.startswith("r#") else written


def attribute_facts(src: bytes, item) -> tuple[bool, str | None, bool]:
    """(has a #[cfg], the #[path = "..."] value, has a #[cfg_attr(.., path = ..)]) of an item's attributes."""
    cfg, path, cfg_path = False, None, False
    for attribute in _attributes(item):
        inner = attribute.named_children[0] if attribute.named_children else None
        if inner is None or not inner.named_children:
            continue
        head = _read(src, inner.named_children[0])
        if head == "cfg":
            cfg = True
        elif head == "path":
            value = inner.child_by_field_name("value")
            if value is not None:
                content = next((c for c in value.named_children if c.type == "string_content"), None)
                path = _read(src, content) if content is not None else _read(src, value).strip('"')
        elif head == "cfg_attr" and re.search(r"\bpath\b", _read(src, inner)):
            cfg_path = True
    return cfg, path, cfg_path


def build_tree(repo: Path, crate: Crate, walked: set[str], parse: Parse) -> Tree:
    """Every module of a crate. A module declared twice under different cfgs is kept twice."""
    tree = Tree()
    if not (repo / crate.root).is_file():
        tree.problems.append(f"{crate.root} (the crate root of {crate.name} does not exist)")
        return tree
    if crate.root not in walked:
        tree.problems.append(f"{crate.root} (the crate root of {crate.name} is outside the files ripple walks)")
        return tree
    read = parse(crate.root)
    if read is None:
        return tree
    src, root_node = read
    root = Module(crate, (), crate.root, root_node, src, None)
    tree.modules.append(root)
    _children(repo, root, root_node, True, (), walked, parse, tree, 0)
    return tree


def _children(repo: Path, module: Module, container, owns_dir: bool, inline: tuple[str, ...], walked: set[str], parse: Parse, tree: Tree, depth: int) -> None:
    if depth > MAX_DEPTH:
        return
    src, file = module.src, module.file
    directory, base_name = posixpath.dirname(file), posixpath.basename(file)
    for item in container.named_children:
        if item.type != "mod_item":
            continue
        name = _name(src, item.child_by_field_name("name"))
        cfg, path_attr, cfg_attr_path = attribute_facts(src, item)
        body = item.child_by_field_name("body")
        child_path = module.path + (name,)
        vis = next((_read(src, c) for c in item.children if c.type == "visibility_modifier"), "")
        if body is not None:
            child = Module(module.crate, child_path, file, body, src, module, module.inline + (name,), cfg or module.cfg, vis, item)
            tree.modules.append(child)
            _children(repo, child, body, owns_dir, inline + (name,), walked, parse, tree, depth + 1)
            continue
        here = directory if owns_dir else _join(directory, posixpath.splitext(base_name)[0])
        if path_attr is not None:
            candidates = [_join(directory, path_attr)] if not inline else [_join(here, *inline, path_attr)]
        else:
            where = _join(here, *inline) if inline else here
            candidates = [_join(where, name + SUFFIX), _join(where, name, "mod.rs")]
        found = [c for c in candidates if (repo / c).is_file()]
        if not found:
            how = " (declared under a cfg_attr path ripple does not read)" if cfg_attr_path else ""
            tree.problems.append(f"{file} (`mod {name};` names no file: {candidates[0]} does not exist{how})")
            continue
        target = found[0]
        if target not in walked:
            tree.problems.append(f"{target} (declared by `mod {name};` in {file}, but outside the files ripple walks)")
            continue
        read = parse(target)
        if read is None:
            continue
        csrc, cnode = read
        child = Module(module.crate, child_path, target, cnode, csrc, module, (), cfg or module.cfg, vis, item)
        tree.modules.append(child)
        _children(repo, child, cnode, posixpath.basename(target) == "mod.rs" or path_attr is not None, (), walked, parse, tree, depth + 1)
