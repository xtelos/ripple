"""Which packages' requirements decide the Cargo.lock (round 2, F2).

Cargo resolves ONE Cargo.lock over every package the root reaches: the workspace members and the transitive closure of their path
dependencies, members or not. A requirement written by any of them (`serde = "=1.0.180"` in a path dependency) decides what the lock
says builds, and a requirement ripple cannot see (a path dependency whose manifest it did not or cannot read) can decide it either way.
Ripple read only the packages of the `[workspace]`, so a stale lock still counted as agreeing and a pin of a precompiled serde_derive
was missed (cargo built the downgrade; `/tmp/momus-r2/cases/m1_pathcap` and `m1_180`).

  * the requirements are collected over the workspace's packages and the closure of their path dependencies (every dependency table,
    every `[target]` table, an inherited `workspace = true` path), a cycle ending where it began;
  * a path dependency whose manifest is not a readable package (outside the analysed directory, missing, unparsable, a virtual manifest,
    a path that is no string) is an unreadable requirement for every crate of the list, so the lock is no lock for the helpers and serde
    is refused with a lock and without;
  * a manifest the walk skipped (a directory with no source file, a hidden one) but that is inside the analysed directory is read, and
    is fingerprinted, so editing it rebuilds the graph.
"""

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_derive_trust import CIO, CLAP, REFUSED, S, TRUSTED, as_before, lock, marked, signature  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple import rs_modules  # noqa: E402
from ripple.cache import fingerprint, load_graph  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PRELUDE_REFUSAL = "name: the standard prelude of the crate may not be the standard library's"  # Index.shadow_problem
SER_SRC = "use serde::Serialize;\n#[derive(Serialize)]\n" + S + "fn main() {}\n"
SERDE = 'serde = { version = "1", features = ["derive"] }\n'
CMD_SRC = "mod other { #[allow(unused_imports)] use x::command; }\nuse clap::Parser;\n#[derive(Parser)]\n#[command(version)]\nstruct Cli;\nfn main() {}\n"
PLAIN_CLAP = "use clap::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n"


def pkg(name, deps="", extra="", lib=""):
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n{lib}{extra}\n[dependencies]\n{deps}'


def sibling(app_deps, other_deps, app_src=SER_SRC, files=None):
    """`app` and `lib2` side by side with no workspace table above them: app has `lib2` as a path dependency, which is no member of anything."""
    return {
        "app/Cargo.toml": pkg("app", app_deps + 'lib2 = { path = "../lib2" }\n'),
        "app/src/main.rs": app_src,
        "lib2/Cargo.toml": pkg("lib2", other_deps),
        "lib2/src/lib.rs": "",
        **(files or {}),
    }


def serde_lock(version="1.0.229"):
    return {"app/Cargo.lock": lock(("serde", version, CIO), ("serde_core", version, CIO), ("serde_derive", version, CIO))}


def clap_lock(version="4.6.7"):
    return {"app/Cargo.lock": lock(("clap", version, CIO), ("clap_derive", version, CIO))}


def workspace_of(make_repo, files, sub=""):
    repo = make_repo(files) / sub
    found, _ = walk_languages(repo, [RUST])
    return rs_modules.load_workspace(repo, found[RUST.name])


def verdict_of(make_repo, files, sub=""):
    repo = make_repo(files) / sub
    found, skipped = walk_languages(repo, [RUST])
    index, _ = rs.read_repo(repo, found[RUST.name], skipped)
    if marked(index):
        return REFUSED
    assert [r for ctx in index.ctxs for r in ctx.attr_reasons()] == []
    return TRUSTED


def reasons_at(make_repo, files, sub=""):
    repo = make_repo(files) / sub
    found, skipped = walk_languages(repo, [RUST])
    index, _ = rs.read_repo(repo, found[RUST.name], skipped)
    return [site.refused for ctx in index.ctxs for site in ctx.derive_sites]


# ---- a path dependency that is no workspace member counts


def test_the_requirements_of_a_path_dependency_that_is_no_member_are_collected(make_repo):
    ws = workspace_of(make_repo, sibling(SERDE, 'serde = "=1.0.228"\n'))
    assert sorted(ws.packages["app"].requirements["serde"]) == ["1", "=1.0.228"]


def test_a_lock_the_pin_of_a_path_dependency_no_longer_meets_is_no_lock(make_repo):
    """m1_pathcap: the lock was written while lib2 asked for `1` and says serde_derive 1.0.229; cargo downgrades it to the 1.0.228 lib2 pins."""
    ws = workspace_of(make_repo, sibling(SERDE, 'serde = "=1.0.228"\n', files=serde_lock()))
    package = ws.packages["app"]
    assert package.locked["serde_derive"] == ("1.0.229",)
    assert not rs.lock_agrees(package, "serde_derive")
    assert rs.usable_locked(package) == {}


def test_the_same_lock_agrees_where_the_path_dependency_asks_for_what_it_lists(make_repo):
    ws = workspace_of(make_repo, sibling(SERDE, 'serde = "1"\n', files=serde_lock()))
    assert rs.lock_agrees(ws.packages["app"], "serde_derive")


@pytest.mark.parametrize("locked", [True, False], ids=["with a lock at 1.0.229", "without a lock"])
def test_a_path_dependency_that_pins_a_precompiled_serde_derive_refuses_the_serde_derive(make_repo, locked):
    """m1_180: serde 1.0.180 pins serde_derive =1.0.180, a precompiled version (cargo builds it, with or without the lock)."""
    files = sibling(SERDE, 'serde = "=1.0.180"\n', files=serde_lock() if locked else None)
    assert verdict_of(make_repo, files) == REFUSED
    [why] = reasons_at(make_repo, files)
    assert "precompiled" in why and "1.0.180" in why, why


def test_a_pin_of_a_path_dependency_that_builds_a_plain_serde_derive_is_trusted_whatever_the_lock_says(make_repo):
    """m1_pathcap's own verdict: cargo downgrades to 1.0.228, not a precompiled version, so the derive is the published one."""
    files = sibling(SERDE, 'serde = "=1.0.228"\n', files=serde_lock())
    assert verdict_of(make_repo, files) == TRUSTED


def test_a_path_dependency_that_pins_an_older_clap_makes_the_lock_no_lock_for_the_helpers(make_repo):
    """app asks clap 4, the path dependency `=4.0.6`, the lock says 4.6.7 (which declares `command`): cargo downgrades, and 4.0.6 declares
    no `group`. The version-independent helpers apply, which do not hold `command`."""
    files = sibling(CLAP, 'clap = "=4.0.6"\n', app_src=CMD_SRC, files=clap_lock())
    assert verdict_of(make_repo, files) == REFUSED
    control = sibling(CLAP, 'clap = "4"\n', app_src=CMD_SRC, files=clap_lock())
    assert verdict_of(make_repo, control) == TRUSTED  # the lock agrees and says 4.6.7: `command` is certain


def test_a_transitive_path_dependency_counts(make_repo):
    files = sibling(SERDE, 'lib3 = { path = "../lib3" }\n') | {"lib3/Cargo.toml": pkg("lib3", 'serde = "=1.0.180"\n'), "lib3/src/lib.rs": ""}
    assert verdict_of(make_repo, files) == REFUSED


@pytest.mark.parametrize(
    "section",
    [
        pytest.param("dependencies", id="dependencies"),
        pytest.param("dev-dependencies", id="dev-dependencies"),
        pytest.param("build-dependencies", id="build-dependencies"),
        pytest.param("target.'cfg(unix)'.dependencies", id="a target table"),
    ],
)
def test_a_path_dependency_in_any_table_of_the_package_counts(make_repo, section):
    app = pkg("app", SERDE) + (f"\n[{section}]\n" if section != "dependencies" else "") + 'lib2 = { path = "../lib2" }\n'
    files = {"app/Cargo.toml": app, "app/src/main.rs": SER_SRC, "lib2/Cargo.toml": pkg("lib2", 'serde = "=1.0.180"\n'), "lib2/src/lib.rs": ""}
    assert verdict_of(make_repo, files) == REFUSED


def test_a_path_dependency_inherited_from_the_workspace_counts(make_repo):
    """`lib2.workspace = true` takes the path the root writes (here a directory beside the workspace: no member of it)."""
    files = {
        "ws/Cargo.toml": '[workspace]\nmembers = ["app"]\n[workspace.dependencies]\nlib2 = { path = "../lib2" }\n',
        "ws/app/Cargo.toml": pkg("app", SERDE + "lib2.workspace = true\n"),
        "ws/app/src/main.rs": SER_SRC,
        "lib2/Cargo.toml": pkg("lib2", 'serde = "=1.0.180"\n'),
        "lib2/src/lib.rs": "",
    }
    assert verdict_of(make_repo, files) == REFUSED


def test_a_cycle_of_path_dependencies_ends(make_repo):
    files = sibling(SERDE, 'lib3 = { path = "../lib3" }\n') | {"lib3/Cargo.toml": pkg("lib3", 'lib2 = { path = "../lib2" }\nserde = "1"\n'), "lib3/src/lib.rs": ""}
    assert verdict_of(make_repo, files) == TRUSTED


def test_a_path_dependency_of_another_workspace_still_counts_for_the_lock_of_the_package_that_reaches_it(make_repo):
    """Its own `[workspace]` is ignored when it is a dependency: it is resolved in the lock of the root that reaches it."""
    files = sibling(SERDE, "") | {"lib2/Cargo.toml": pkg("lib2", 'serde = "=1.0.180"\n', extra="\n[workspace]\n")}
    assert verdict_of(make_repo, files) == REFUSED


def test_a_path_dependency_that_agrees_changes_nothing(make_repo):
    files = sibling(SERDE, 'serde = "1"\n', files=serde_lock())
    assert verdict_of(make_repo, files) == TRUSTED
    assert rs.usable_locked(workspace_of(make_repo, files).packages["app"]).keys() >= {"serde_derive"}


# ---- a path dependency ripple cannot read is an unreadable requirement


def outside(app_deps, app_src=SER_SRC, files=None):
    """The analysed directory is `repo/`; its package depends on one beside it, which ripple never reads."""
    return {
        "repo/app/Cargo.toml": pkg("app", app_deps + 'far = { path = "../../far" }\n'),
        "repo/app/src/main.rs": app_src,
        "far/Cargo.toml": pkg("far", 'serde = "=1.0.180"\nclap = "=4.0.6"\n'),
        "far/src/lib.rs": "",
        **(files or {}),
    }


@pytest.mark.parametrize("locked", [True, False], ids=["with a lock", "without a lock"])
def test_a_path_dependency_outside_the_analysed_directory_refuses_serde(make_repo, locked):
    files = outside(SERDE, files={"repo/app/Cargo.lock": serde_lock()["app/Cargo.lock"]} if locked else None)
    assert verdict_of(make_repo, files, "repo") == REFUSED
    [why] = reasons_at(make_repo, files, "repo")
    assert "precompiled" in why and "cannot" in why, why


def test_a_path_dependency_outside_the_analysed_directory_leaves_a_lock_no_lock_for_the_helpers(make_repo):
    lock_file = {"repo/app/Cargo.lock": clap_lock()["app/Cargo.lock"]}
    assert verdict_of(make_repo, outside(CLAP, app_src=CMD_SRC, files=lock_file), "repo") == REFUSED
    # a derive with no helper attribute asks nothing of the lock, so it stays trusted
    assert verdict_of(make_repo, outside(CLAP, app_src=PLAIN_CLAP, files=lock_file), "repo") == TRUSTED


def test_every_crate_of_the_list_gets_the_unreadable_requirement(make_repo):
    ws = workspace_of(make_repo, outside(SERDE + CLAP), "repo")
    assert all(None in ws.packages["app"].requirements[c] for c in rs_modules.REQUIRED_CRATES)


BROKEN_TYPES = '[package]\nname = "far"\nversion = "0.1.0"\n[dependencies]\nx = { package = [] }\n'
# name -> (files inside the analysed directory `repo/`, the path the dependency writes)
UNREADABLE = {
    "a path outside the analysed directory": ({}, "../../far"),
    "an absolute path": ({}, "/nonexistent/far"),
    "a directory with no manifest": ({}, "../nowhere"),
    "a manifest that is not TOML": ({"repo/far/Cargo.toml": "[package\nname = 'far'"}, "../far"),
    "a manifest that is not TOML, in a directory with a source file": ({"repo/far/Cargo.toml": "[package\nname = 'far'", "repo/far/src/lib.rs": ""}, "../far"),
    "a manifest that is no package": ({"repo/far/Cargo.toml": "[workspace]\nmembers = []\n"}, "../far"),
    "a package whose fields have the wrong type": ({"repo/far/Cargo.toml": BROKEN_TYPES}, "../far"),
    "a package whose fields have the wrong type, in a directory with a source file": ({"repo/far/Cargo.toml": BROKEN_TYPES, "repo/far/src/lib.rs": ""}, "../far"),
}


@pytest.mark.parametrize("locked", [True, False], ids=["with a lock", "without a lock"])
@pytest.mark.parametrize("name", sorted(UNREADABLE))
def test_a_path_dependency_that_is_not_a_readable_package_refuses_serde_with_and_without_a_lock(make_repo, name, locked):
    extra, path = UNREADABLE[name]
    files = {"repo/app/Cargo.toml": pkg("app", SERDE + f'far = {{ path = "{path}" }}\n'), "repo/app/src/main.rs": SER_SRC, **extra}
    if locked:
        files["repo/app/Cargo.lock"] = serde_lock()["app/Cargo.lock"]
    assert verdict_of(make_repo, files, "repo") == REFUSED


def test_a_path_that_is_no_string_is_unreadable(make_repo):
    files = {"app/Cargo.toml": pkg("app", SERDE + "far = { path = 5 }\n"), "app/src/main.rs": SER_SRC}
    assert verdict_of(make_repo, files) == REFUSED


def hidden(pin, directory=".far"):
    """A package with no source file in its directory (a hidden one here, which the walk skips as well)."""
    return {
        "app/Cargo.toml": pkg("app", SERDE + f'far = {{ path = "../{directory}" }}\n'),
        "app/src/main.rs": SER_SRC,
        f"{directory}/Cargo.toml": pkg("far", f'serde = "{pin}"\n', lib='[lib]\npath = "../app/src/far.rs"\n'),
        "app/src/far.rs": "",
    }


# ---- a workspace member the walk gave no source file to

SKIPPED_LIB = '[lib]\npath = "../app/src/far.rs"\n'


def members_of(entries, member_files=None, locked=False):
    """A workspace of `app` and whatever the `members` entries name; `member_files` are the files of those members."""
    files = {
        "Cargo.toml": "[workspace]\nmembers = [" + ", ".join(f'"{e}"' for e in entries) + "]\n",
        "app/Cargo.toml": pkg("app", SERDE),
        "app/src/main.rs": SER_SRC,
        "app/src/far.rs": "",
        **(member_files or {}),
    }
    if locked:
        files["Cargo.lock"] = lock(("serde", "1.0.229", CIO), ("serde_core", "1.0.229", CIO), ("serde_derive", "1.0.229", CIO))
    return files


def skipped_member(directory, pin):
    return {f"{directory}/Cargo.toml": pkg("far", f'serde = "{pin}"\n', lib=SKIPPED_LIB)}


def hidden_member(pin, directory=".far"):
    """What `hidden` is, reached as a member of the workspace instead of as a path dependency of `app`."""
    return members_of(["app", directory], skipped_member(directory, pin))


@pytest.mark.parametrize("locked", [True, False], ids=["with a lock", "without a lock"])
@pytest.mark.parametrize(
    "entries, directory",
    [
        pytest.param(["app", "crates/*"], "crates/far", id="a glob member"),
        pytest.param(["app", "*"], ".far", id="a hidden directory a glob matches"),
        pytest.param(["app", "far"], "far", id="a member named outright"),
        pytest.param(["app", ".far"], ".far", id="a hidden member named outright"),
        pytest.param(["app", "crates/f?r"], "crates/far", id="a glob with ? in it"),
        pytest.param(["app", "crates/[f]ar"], "crates/far", id="a glob with a class in it"),
    ],
)
def test_a_workspace_member_the_walk_gave_no_source_file_to_counts(make_repo, entries, directory, locked):
    assert verdict_of(make_repo, members_of(entries, skipped_member(directory, "=1.0.180"), locked)) == REFUSED
    assert verdict_of(make_repo, members_of(entries, skipped_member(directory, "=1.0.228"), locked)) == TRUSTED


def test_a_member_directory_with_no_manifest_that_a_glob_matches_is_no_member(make_repo):
    files = members_of(["app", "crates/*"], {"crates/docs/README.md": "text\n", **skipped_member("crates/far", "1")})
    assert verdict_of(make_repo, files) == TRUSTED


def test_a_glob_that_matches_nothing_is_no_requirement(make_repo):
    assert verdict_of(make_repo, members_of(["app", "nothing/*"])) == TRUSTED


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param(["app", "../elsewhere"], id="a member above the analysed directory"),
        pytest.param(["app", "missing"], id="a member named outright that is not there"),
        pytest.param(["app", "crates/**"], id="a ** glob, which ripple does not expand"),
    ],
)
def test_a_member_ripple_cannot_read_is_an_unreadable_requirement(make_repo, entries):
    assert verdict_of(make_repo, members_of(entries)) == REFUSED


def test_a_members_entry_that_is_no_list_is_unreadable(make_repo):
    files = members_of(["app"]) | {"Cargo.toml": '[workspace]\nmembers = "app"\n'}
    assert verdict_of(make_repo, files) == REFUSED


def test_a_workspace_that_lists_only_members_the_walk_read_changes_nothing(make_repo):
    assert verdict_of(make_repo, members_of(["app"], locked=True)) == TRUSTED


def test_the_manifest_of_such_a_member_is_in_the_cache_fingerprint(make_repo):
    repo = make_repo(members_of(["app", "crates/*"], skipped_member("crates/far", "=1.0.180")))
    assert "crates/far/Cargo.toml" in fingerprint(repo, RUST)


# ---- a refusal by requirements reads exactly as the same derive did before the rule

# calls beside the derive that its marks would change (no glob: a glob would refuse the serde import by itself and prove nothing here)
HERE = "struct T;\ntrait Tr {\n    fn make();\n}\nimpl Tr for T {\n    fn make() {}\n}\nfn f() {\n    let _ = String::new();\n    T::make();\n}\n"
HERE_EDGE = "app/src/main:f", "app/src/main:<T as Tr>::make"


def with_here(files, path):
    return {**files, path: files[path] + HERE}


def outside_with(path, extra=None, locked=False):
    files = {"repo/app/Cargo.toml": pkg("app", SERDE + f'far = {{ path = "{path}" }}\n'), "repo/app/src/main.rs": SER_SRC, **(extra or {})}
    if locked:
        files["repo/app/Cargo.lock"] = serde_lock()["app/Cargo.lock"]
    return files


REFUSED_BY_REQUIREMENTS = {
    # name -> (files, analysed subdirectory, the file the probe calls go to)
    "a path dependency pins serde_derive 1.0.180, with a lock": (sibling(SERDE, 'serde = "=1.0.180"\n', files=serde_lock()), "", "app/src/main.rs"),
    "a path dependency pins serde_derive 1.0.180, no lock": (sibling(SERDE, 'serde = "=1.0.180"\n'), "", "app/src/main.rs"),
    "a transitive path dependency pins it": (
        sibling(SERDE, 'lib3 = { path = "../lib3" }\n') | {"lib3/Cargo.toml": pkg("lib3", 'serde = "=1.0.180"\n'), "lib3/src/lib.rs": ""}, "", "app/src/main.rs",
    ),
    "a path dependency outside the directory, with a lock": (outside_with("../../far", locked=True), "repo", "repo/app/src/main.rs"),
    "a path dependency outside the directory, no lock": (outside_with("../../far"), "repo", "repo/app/src/main.rs"),
    "a path dependency whose manifest is not TOML": (outside_with("../far", {"repo/far/Cargo.toml": "[package\n"}), "repo", "repo/app/src/main.rs"),
    "a skipped manifest pins it": (hidden("=1.0.180"), "", "app/src/main.rs"),
    "a workspace member the walk skipped pins it": (members_of(["app", "crates/*"], skipped_member("crates/far", "=1.0.180"), locked=True), "", "app/src/main.rs"),
    "a workspace member above the directory": (members_of(["app", "../elsewhere"]), "", "app/src/main.rs"),
}


@pytest.mark.parametrize("name", sorted(REFUSED_BY_REQUIREMENTS))
def test_a_derive_refused_by_the_requirements_reads_exactly_as_the_same_derive_did_before_the_rule(make_repo, monkeypatch, name):
    files, sub, path = REFUSED_BY_REQUIREMENTS[name]
    files = with_here(files, path)
    repo = make_repo(files) / sub
    found, skipped = walk_languages(repo, [RUST])
    got = signature(*rs.read_repo(repo, found[RUST.name], skipped))
    index, _ = rs.read_repo(repo, found[RUST.name], skipped)
    [(ctx, why)] = [(ctx, site.refused) for ctx in index.ctxs for site in ctx.derive_sites]
    # refused by the requirements, not by something else that happens to catch it. A dependency whose library ripple cannot read (outside the
    # directory, unparsable, a directory the walk skipped) also makes `std` and the prelude's `derive` unknown (#30), and that guard comes first
    # in the reason; the requirements' own verdict is asked of the package, so it is pinned whichever guard names the refusal.
    assert why and ("precompiled" in why or why.startswith(PRELUDE_REFUSAL)), why
    assert rs.precompiled_problem(ctx.crate.package) is not None
    as_before(monkeypatch)
    assert got == signature(*rs.read_repo(repo, found[RUST.name], skipped))


def test_the_comparison_above_is_not_vacuous(make_repo, monkeypatch):
    """Where the requirements are met the derive is trusted, and `T::make()` is an edge that the old reading leaves unresolved."""
    files = with_here(sibling(SERDE, 'serde = "1"\n', files=serde_lock()), "app/src/main.rs")
    repo = make_repo(files)
    found, skipped = walk_languages(repo, [RUST])
    trusted = signature(*rs.read_repo(repo, found[RUST.name], skipped))
    as_before(monkeypatch)
    before = signature(*rs.read_repo(repo, found[RUST.name], skipped))
    assert HERE_EDGE in {(caller, callee) for caller, _, callee in trusted[0]} and HERE_EDGE not in {(c, d) for c, _, d in before[0]}


@pytest.mark.parametrize("locked", [True, False])
def test_a_helper_the_lock_no_longer_vouches_for_reads_as_before(make_repo, monkeypatch, locked):
    """clap: the path dependency pins an older clap, so `#[command]` is no certain helper and the derive is no site (the base's reading)."""
    files = with_here(sibling(CLAP, 'clap = "=4.0.6"\n', app_src=CMD_SRC, files=clap_lock() if locked else None), "app/src/main.rs")
    repo = make_repo(files)
    found, skipped = walk_languages(repo, [RUST])
    got = signature(*rs.read_repo(repo, found[RUST.name], skipped))
    assert reasons_of_at(repo, found, skipped) == []
    as_before(monkeypatch)
    assert got == signature(*rs.read_repo(repo, found[RUST.name], skipped))


def reasons_of_at(repo, found, skipped):
    index, _ = rs.read_repo(repo, found[RUST.name], skipped)
    return [site.refused for ctx in index.ctxs for site in ctx.derive_sites]


# ---- a manifest the walk skipped, inside the analysed directory, is read and fingerprinted


@pytest.mark.parametrize("directory", [".far", "far"])
def test_a_manifest_the_walk_skipped_is_read_for_its_requirements(make_repo, directory):
    """Reached as a workspace member: the verdict follows what it pins."""
    assert verdict_of(make_repo, hidden_member("=1.0.180", directory)) == REFUSED
    assert verdict_of(make_repo, hidden_member("=1.0.228", directory)) == TRUSTED
    assert verdict_of(make_repo, hidden_member("1", directory)) == TRUSTED


@pytest.mark.parametrize("pin", ["=1.0.180", "=1.0.228", "1"])
@pytest.mark.parametrize("directory", [".far", "far"])
def test_a_manifest_the_walk_skipped_is_read_for_its_requirements_as_a_path_dependency_too(make_repo, directory, pin):
    """Its pin is read all the same (the requirements are what the lock must meet), but a path dependency whose library ripple did not read may
    be called `std` (#30), so the derive is refused whatever it pins: the verdict cannot show the reading, the requirements do."""
    files = hidden(pin, directory)
    assert sorted(workspace_of(make_repo, files).packages["app"].requirements["serde"]) == sorted(["1", pin])
    assert verdict_of(make_repo, files) == REFUSED


def test_such_a_manifest_is_in_the_cache_fingerprint(make_repo):
    repo = make_repo(hidden("=1.0.180"))
    assert ".far/Cargo.toml" in fingerprint(repo, RUST)
    assert rs_modules.config_files(repo, walk_languages(repo, [RUST])[0][RUST.name]).count(".far/Cargo.toml") == 1


def test_editing_such_a_manifest_rebuilds_the_graph_with_the_new_verdict(make_repo, tmp_path):
    src = SER_SRC.replace("fn main() {}", "trait Tr {\n    fn make();\n}\nimpl Tr for S {\n    fn make() {}\n}\nfn main() {\n    S::make();\n}\n")
    files = hidden_member("=1.0.228") | {"app/src/main.rs": src}  # (as a path dependency it would be refused for its library: see above)
    repo = make_repo(files)
    cache = tmp_path / "cache"
    graph, cached = load_graph(repo, cache, profile=RUST)
    edge = {("app/src/main:main", "app/src/main:<S as Tr>::make")}
    assert not cached and {(e.caller, e.callee) for e in graph.edges if e.kind == "call"} == edge
    assert load_graph(repo, cache, profile=RUST)[1] is True
    Path(repo, ".far/Cargo.toml").write_text(files[".far/Cargo.toml"].replace("=1.0.228", "=1.0.180"))
    graph, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and {(e.caller, e.callee) for e in graph.edges if e.kind == "call"} == set()


def test_a_manifest_of_a_member_that_appears_later_rebuilds_the_graph(make_repo, tmp_path):
    """The member's manifest was missing (unreadable, refused); creating it changes the set of files the answers depend on."""
    src = SER_SRC.replace("fn main() {}", "trait Tr {\n    fn make();\n}\nimpl Tr for S {\n    fn make() {}\n}\nfn main() {\n    S::make();\n}\n")
    files = hidden_member("=1.0.228") | {"app/src/main.rs": src}
    manifest_text = files.pop(".far/Cargo.toml")
    repo = make_repo(files)
    cache = tmp_path / "cache"
    first, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and {(e.caller, e.callee) for e in first.edges if e.kind == "call"} == set()  # no manifest: unreadable, refused
    Path(repo, ".far").mkdir()
    Path(repo, ".far/Cargo.toml").write_text(manifest_text)
    second, cached = load_graph(repo, cache, profile=RUST)
    assert not cached and {(e.caller, e.callee) for e in second.edges if e.kind == "call"} == {("app/src/main:main", "app/src/main:<S as Tr>::make")}


def test_a_package_called_serde_in_a_skipped_manifest_refuses_the_derive_as_a_package_of_the_repo_called_so_does(make_repo):
    """The skipped package is a package of the repo all the same: its name and its library name are among those the guards read."""
    files = hidden_member("1") | {".far/Cargo.toml": pkg("serde", "", lib='[lib]\npath = "../app/src/far.rs"\n')}
    assert verdict_of(make_repo, files) == REFUSED
    [why] = reasons_at(make_repo, files)
    assert "called serde" in why or "library called" in why, why
