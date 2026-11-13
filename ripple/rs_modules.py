"""Which crates a Rust repo holds, what each is called, and which file is each of its modules.

A module is its file path without the extension, with `x/mod.rs` standing for `x` (`src/cli`, and
`src/lib` for the crate root, which is never folded: a package may hold both a lib.rs and a main.rs).
A symbol inside it follows a colon, its own path inside the file after that (`src/cli:parse_args`,
`src/cli:Opts::new`, `src/cli:tests::it_works`).

Cargo.toml is read for the crate graph: every package, the crates it builds (the library, the binaries,
tests, examples, benches and build script, found as cargo finds them, with `[lib]` and `[[bin]]` path
overrides), and what each package can name by `use`: its dependencies by the name they are written
under (a hyphen is an underscore, a renamed one is its key), and which of them are packages of this
repo (a `path` dependency, a `workspace = true` one that the workspace gives a path, a `[patch]`).
Everything else a package names is outside the repo.

The module tree of a crate starts at its root file and follows `mod` declarations: `mod x;` is x.rs
or x/mod.rs beside the declaring file (or inside the directory a non-mod.rs file owns), `#[path]`
names the file outright, and `mod x { ... }` is a module in the same file. A declaration written by
a macro, under a `cfg_attr(.., path = ..)`, or naming no file leaves its file out, and says so
(Tree.problems), as does a file the walk did not list. No parser is needed here: the tree is built from
the nodes a caller's `parse` hands back, and Cargo.toml needs only tomllib (tomli before Python 3.11).
"""

from __future__ import annotations

import os
import posixpath
import re
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
    manifest and its workspace's are among these; a manifest above no source file changes nothing."""
    return sorted(
        (f"{d}/Cargo.toml" if d else "Cargo.toml") for d in _source_dirs(repo, sources) if (repo / d / "Cargo.toml").is_file()
    )


CARGO_CONFIGS = (".cargo/config.toml", ".cargo/config")


def config_files(repo: Path, sources: list[Path]) -> list[str]:
    """The files besides the sources that the index depends on, which the cache fingerprints (registry.py): the manifests, the
    Cargo.lock beside each, and every cargo config file `cargo_configs` finds (what `_trust` reads to prove an attribute
    macro is the published crate's)."""
    out = manifests(repo, sources)
    for d in _source_dirs(repo, sources):
        lock = _join(d, "Cargo.lock")
        if (repo / d / "Cargo.toml").is_file() and (repo / lock).is_file():
            out.append(lock)
    return sorted({*out, *cargo_configs(repo)})


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
    deps: dict[str, str | None]  # extern name -> the dir of the repo package it is, None when it is outside the repo
    manifest: dict
    edition: str
    trust: dict[str, str | None] = field(default_factory=dict)  # crate of TRUSTED_CRATES -> None when the repo proves it the published one, else why not


@dataclass
class Crate:
    name: str
    kind: str  # lib | bin | test | example | bench | build
    root: str  # repo-relative file
    package: Package


@dataclass
class Workspace:
    packages: dict[str, Package] = field(default_factory=dict)  # dir -> package
    crates: list[Crate] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # manifests that could not be read


def load_workspace(repo: Path, sources: list[Path]) -> Workspace:
    """Every package and crate of the repo, read from the Cargo.toml files above its sources."""
    toml = _toml()
    ws = Workspace()
    data: dict[str, dict] = {}
    unreadable: set[str] = set()  # the directories whose Cargo.toml could not be read: nothing at or below them is proven
    for rel in manifests(repo, sources):
        try:
            data[posixpath.dirname(rel)] = toml.loads((repo / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            ws.problems.append(f"{rel} (Cargo.toml could not be read)")
            unreadable.add(posixpath.dirname(rel))
    for pdir in sorted(data):
        if isinstance(data[pdir].get("package"), dict) and isinstance(data[pdir]["package"].get("name"), str):
            try:
                ws.packages[pdir] = _package(pdir, data)
            except (AttributeError, TypeError, ValueError, KeyError) as error:
                ws.problems.append(_wrong_type(pdir, error))
    names = {_norm(p.name) for p in ws.packages.values()}
    # the config files are read once, and only for a repo whose packages name a crate of TRUSTED_CRATES at all
    asks = any(_norm(key) in TRUSTED_CRATES for p in ws.packages.values() for section in _dependency_sections(p.manifest) if isinstance(section, dict) for key in section)
    config_why = _config_problem(repo) if asks else None
    for pdir, package in ws.packages.items():
        try:
            package.trust = _trust(repo, pdir, data, names, unreadable, config_why)
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            package.trust = {c: f"the manifest could not be read for this ({type(error).__name__})" for c in TRUSTED_CRATES}
    for pdir, package in list(ws.packages.items()):
        try:
            ws.crates += _targets(repo, package)
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            ws.problems.append(_wrong_type(pdir, error))
            del ws.packages[pdir]
    return ws


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


def _package(pdir: str, data: dict[str, dict]) -> Package:
    manifest = data[pdir]
    meta = manifest["package"]
    root = _workspace_root(pdir, data)
    shared = data[root].get("workspace", {}) if root is not None else {}
    shared = shared if isinstance(shared, dict) else {}
    shared_deps = shared.get("dependencies", {}) if isinstance(shared.get("dependencies"), dict) else {}
    patches = {}
    patch_table = data[root].get("patch", {}) if root is not None else {}
    for registry in (patch_table.values() if isinstance(patch_table, dict) else ()):
        if isinstance(registry, dict):
            for name, spec in registry.items():
                if isinstance(spec, dict) and isinstance(spec.get("path"), str):
                    patches[name] = _join(root, spec["path"])
    edition = meta.get("edition", "2015")
    if isinstance(edition, dict):  # edition.workspace = true
        shared_package = shared.get("package", {})
        edition = shared_package.get("edition", "2015") if isinstance(shared_package, dict) else "2015"
    # which of the packages of the repo have a manifest: the dir a path names must be one of them
    known = {d for d in data if isinstance(data[d].get("package"), dict)}
    deps: dict[str, str | None] = {}

    def add(key: str, target: str | None) -> None:
        name = key.replace("-", "_")
        if name in deps and deps[name] != target:
            deps[name] = AMBIGUOUS
        else:
            deps[name] = target

    sections = []
    targets = manifest.get("target", {})
    for scope in (manifest, *(t for t in (targets.values() if isinstance(targets, dict) else ()) if isinstance(t, dict))):
        sections += [scope.get(k, {}) for k in ("dependencies", "dev-dependencies", "build-dependencies")]
    for section in sections:
        if not isinstance(section, dict):
            continue
        for key, spec in section.items():
            real, path = key, None
            if isinstance(spec, dict):
                real = spec.get("package", key)
                if spec.get("workspace") and isinstance(shared_deps.get(key), dict):
                    wide = shared_deps[key]
                    real = wide.get("package", real)
                    if isinstance(wide.get("path"), str) and root is not None:
                        path = _join(root, wide["path"])
                elif isinstance(spec.get("path"), str):
                    path = _join(pdir, spec["path"])
            if path is None and real in patches:
                path = patches[real]
            add(key, path if path in known else None)
    lib = manifest.get("lib")
    lib_name = None  # a package with no [lib] table has a library only if src/lib.rs exists (see _targets)
    if "lib" in manifest:
        lib_name = str(lib.get("name", meta["name"]) if isinstance(lib, dict) else meta["name"]).replace("-", "_")
    return Package(pdir, meta["name"], lib_name, deps, manifest, str(edition))


# ---- whether a dependency is the published crate
#
# ripple trusts three attribute macros (rs_indexer.TRUSTED_ATTRS) only where the repo shows that the crate that defines
# each is the one on crates.io. Each is the macro of a crate (`tokio::main` is tokio's, written by tokio-macros) and the
# proof covers both: the dependency as the package's manifests write it, what a workspace, a lock or a cargo config
# file may replace it with, and that no package of the repo is called by the name of either. What lives outside the
# repo (a config file above it, RUSTFLAGS, a wrapper, a registry mirror that serves other code) cannot be seen and is
# the one assumption the README states.

TRUSTED_CRATES = {"tokio": "tokio-macros", "serial_test": "serial_test_derive"}  # crate -> the crate that defines its macros
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


def _config_table_problem(conf: dict) -> str | None:
    for key, value in conf.items():
        if key not in CONFIG_KEYS:
            return f"the key `{key}`"
        allowed = CONFIG_KEYS[key]
        if key == "env":
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
    `.cargo`), repo-relative and sorted, wherever it is, with the names matched in any case (cargo on a case-insensitive file system
    reads `.Cargo/Config.toml`; reading it everywhere only refuses more). The walk follows a link to a directory that is inside the
    repo (each directory once, by its real path, so a loop ends) and reads a `.cargo` that is a link wherever it points; it does
    not enter a link that leaves the repo, which cargo reads for no package of the repo. It skips `.git` and `node_modules`, and a `target`
    directory with a Cargo.toml beside it, which is where the source walk reads no file either (rs_indexer.reads_as_source): one
    with none beside it is a directory like any other, and a package may sit in it."""
    root = os.path.realpath(repo)
    found: list[str] = []
    visited = {root}
    stack = [(os.fspath(repo), "", root)]  # (path as walked, repo-relative path, real path)
    while stack:
        here, rel, real = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        manifest = any(e.name == "Cargo.toml" and e.is_file() for e in entries)
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
    return sorted(set(found))


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _config_names(directory: str, rel: str) -> list[str]:
    """The config files in one `.cargo` directory, as the names are written there."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [f"{rel}/{n}" for n in names if n.lower() in ("config", "config.toml") and os.path.isfile(os.path.join(directory, n))]


def _config_problem(repo: Path) -> str | None:
    """Why a cargo config file `cargo_configs` finds may change what a crate or `rustc` is: it cannot be read, or sets something that
    is not in CONFIG_KEYS (or a rustflag, or an env entry, that is not allowed)."""
    toml = _toml()
    for rel in cargo_configs(repo):
        try:
            conf = toml.loads((repo / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return f"{rel} could not be read"
        if (why := _config_table_problem(conf)) is not None:
            return f"{rel} sets {why}"
    return None


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


def _trust(repo: Path, pdir: str, data: dict[str, dict], names: set[str], unreadable: set[str], config_why: str | None) -> dict[str, str | None]:
    """For each crate of TRUSTED_CRATES, None when the repo proves that this package's use of it is the published crate (from
    crates.io, unrenamed, unpatched, and its macro crate too), else why it does not."""
    manifest = data[pdir]
    root = _workspace_root(pdir, data)
    top = data[root] if root is not None else manifest
    base = root if root is not None else pdir
    shared = top.get("workspace", {}) if isinstance(top.get("workspace"), dict) else {}
    shared_deps = shared.get("dependencies", {}) if isinstance(shared.get("dependencies"), dict) else {}
    sections = _dependency_sections(manifest)
    scope_why = _scope_problem(pdir, data, unreadable)
    locked = _lock_packages(repo, base)
    out: dict[str, str | None] = {}
    for crate, macro_crate in TRUSTED_CRATES.items():
        why = _dependency_problem(sections, shared_deps, crate, macro_crate)
        if not why and names & {crate, _norm(macro_crate)}:
            why = f"a package of the repo is called {crate} or {macro_crate}"
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

    out: list[Crate] = []
    src_lib = _join(base, "src/lib.rs")
    if package.lib_name is None and exists(src_lib):
        package.lib_name = package.name.replace("-", "_")
    if package.lib_name:
        lib = manifest.get("lib", {}) if isinstance(manifest.get("lib"), dict) else {}
        out.append(Crate(package.lib_name, "lib", _join(base, written(lib, "path", "src/lib.rs")), package))
    seen: set[str] = set()
    for entry in listed("bin"):
        name = written(entry, "name", package.name)
        path = written(entry, "path") or next(
            (c for c in (f"src/bin/{name}.rs", f"src/bin/{name}/main.rs", "src/main.rs") if exists(_join(base, c))), None
        )
        if path:
            root = _join(base, path)
            out.append(Crate(name.replace("-", "_"), "bin", root, package))
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
            out.append(Crate(entry_name.replace("-", "_"), kind, root, package))
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
