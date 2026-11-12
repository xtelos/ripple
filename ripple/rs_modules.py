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


def manifests(repo: Path, sources: list[Path]) -> list[str]:
    """Every Cargo.toml in a directory that holds or sits above a source file, repo-relative. A package's own
    manifest and its workspace's are among these; a manifest above no source file changes nothing."""
    dirs: set[str] = {""}
    for path in sources:
        try:
            rel = path.relative_to(repo).parent
        except ValueError:
            continue
        for parent in (rel, *rel.parents):
            dirs.add("" if parent == Path(".") else parent.as_posix())
    return sorted(
        (f"{d}/Cargo.toml" if d else "Cargo.toml") for d in dirs if (repo / d / "Cargo.toml").is_file()
    )


def config_files(repo: Path, sources: list[Path]) -> list[str]:
    """The files besides the sources that the index depends on, which the cache fingerprints (registry.py)."""
    return manifests(repo, sources)


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
    for rel in manifests(repo, sources):
        try:
            data[posixpath.dirname(rel)] = toml.loads((repo / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            ws.problems.append(f"{rel} (Cargo.toml could not be read)")
    for pdir in sorted(data):
        if isinstance(data[pdir].get("package"), dict) and isinstance(data[pdir]["package"].get("name"), str):
            try:
                ws.packages[pdir] = _package(pdir, data)
            except (AttributeError, TypeError, ValueError, KeyError) as error:
                ws.problems.append(_wrong_type(pdir, error))
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
