"""Holes an adversarial review found in the trusted derives, each built and run against the real crates (serde 1.0.228, clap 4.6.7,
thiserror 2.0.21) and each a wrong edge on the first version of the rule:

  * a `use` that renames another item to a derive's name was trusted as that derive (H1);
  * a package of the repo whose library is called like a crate of the list captures the crate's name (H2);
  * a lock that disagrees with the manifest decided the helpers and the precompiled check (M1);
  * a target-level `edition` got past the edition gate (M2);
  * a refused site changed the order the base's reasons come in (L1).

Every test here fails on the commit before the one that fixes it.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_derive_trust import CIO, CLAP, REFUSED, S, SERDE, TE, TRUSTED, as_before, lib, lock, main, manifest, read, reasons_of, signature, verdict  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402
from ripple import rs_modules  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402


def calls(make_repo, files):
    _, graph = read(make_repo, files)
    return sorted((e.caller, e.callee) for e in graph.edges), sorted((u.caller, u.text, u.reason) for u in graph.unresolved)


# ---- H1: the import is the derive's own name, from a crate that publishes it


@pytest.mark.parametrize(
    "src, deps",
    [
        pytest.param("use serde::Deserialize as Serialize;\n#[derive(Serialize)]\n" + S, SERDE, id="serde: Deserialize renamed Serialize"),
        pytest.param("use serde::Serialize as Deserialize;\n#[derive(Deserialize)]\n" + S, SERDE, id="serde: Serialize renamed Deserialize"),
        pytest.param("use serde::ser::Serialize;\n#[derive(Serialize)]\n" + S, SERDE, id="serde: three segments, the trait of the module"),
        pytest.param("use serde::Serialize::Serialize;\n#[derive(Serialize)]\n" + S, SERDE, id="serde: a deeper path ending in the name"),
        pytest.param("use clap::Args as Parser;\n#[derive(Parser)]\nstruct Cli;\n", CLAP, id="clap: Args renamed Parser"),
        pytest.param("use clap::Subcommand as ValueEnum;\n#[derive(ValueEnum)]\nenum Cli {\n    A,\n}\n", CLAP, id="clap: Subcommand renamed ValueEnum"),
        pytest.param("use clap::builder::Parser;\n#[derive(Parser)]\nstruct Cli;\n", CLAP, id="clap: three segments"),
        pytest.param("use thiserror::__private21 as Error;\n#[derive(Debug, Error)]\n" + S, TE, id="thiserror: a module renamed Error"),
        pytest.param("use thiserror::__private21::Error;\n#[derive(Debug, Error)]\n" + S, TE, id="thiserror: three segments"),
        pytest.param("use ::serde::Deserialize as Serialize;\n#[derive(Serialize)]\n" + S, SERDE, id="a leading :: does not make a rename the name"),
    ],
)
def test_an_import_that_is_not_exactly_the_crates_path_to_the_derives_own_name_is_refused(make_repo, src, deps):
    files = main(src + "fn main() {}\n", deps) if "clap" in deps else lib(src, deps)
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "is imported as" in why, why


@pytest.mark.parametrize("name, crate, deps, item", [("Serialize", "serde", SERDE, S), ("Parser", "clap", CLAP, "struct Cli;\n"), ("Error", "thiserror", TE, S)])
def test_the_crates_own_path_to_the_name_is_still_trusted_also_when_it_says_the_same_name_again(make_repo, name, crate, deps, item):
    for use in (f"use {crate}::{name};", f"use ::{crate}::{name};", f"use {crate}::{name} as {name};"):
        src = f"{use}\n#[derive({name})]\n{item}"
        files = main(src + "fn main() {}\n", deps) if crate == "clap" else lib(src, deps)
        assert verdict(make_repo, files) == TRUSTED, use


def test_a_renamed_serde_derive_does_not_draw_the_edge_to_the_trait_impl_serdes_own_remote_function_beats(make_repo):
    """`use serde::Deserialize as Serialize;` then `#[derive(Serialize)] #[serde(remote = ..)]` is Deserialize, whose remote mode adds an
    inherent `deserialize` (cargo run: `inherent from the derive`). DERIVE_FNS is keyed by the name as written, which was `Serialize`."""
    files = lib(
        "use serde::Deserialize as Serialize;\npub struct Other;\npub trait Tr {\n    fn deserialize() -> u8;\n}\n#[derive(Serialize)]\n#[serde(remote = \"Other\")]\n"
        + S + "impl Tr for S {\n    fn deserialize() -> u8 {\n        1\n    }\n}\nfn main() {\n    S::deserialize();\n}\n"
    )
    edges, _ = calls(make_repo, files)
    assert edges == []


def test_a_renamed_clap_derive_does_not_make_a_helper_of_a_name_the_real_derive_does_not_declare(make_repo):
    """`use clap::Args as Parser;` is Args, which declares no `structopt`: beside an attribute macro of that name the site must stay read
    as it always was."""
    files = main("use clap::Args as Parser;\nuse other::structopt;\n#[derive(Parser)]\n#[structopt(name = \"x\")]\nstruct Cli;\nfn main() {}\n", CLAP + 'other = "1"\n')
    assert verdict(make_repo, files) == REFUSED


def test_a_renamed_thiserror_import_beside_a_glob_that_brings_another_error_derive_is_not_thiserrors(make_repo):
    files = lib("use thiserror::__private21 as Error;\nuse other::*;\n#[derive(Debug, Error)]\n" + S, TE + 'other = "1"\n')
    assert verdict(make_repo, files) == REFUSED
    assert "is imported as" in reasons_of(make_repo, files)[0]


# ---- H2: a library of the repo called like a crate of the list


def member(name, lib_name=None, lib_extra="", body=""):
    """A package of the repo, with a `[lib]` table when it names its library."""
    table = f'\n[lib]\nname = {lib_name!r}\n{lib_extra}' if lib_name is not None else ""
    return {f"{name}/Cargo.toml": f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n{table}', f"{name}/src/lib.rs": body}


def workspace(app_src, app_deps, *members, extra_members=()):
    names = ["app", *(m for m in extra_members)]
    files = {"Cargo.toml": "[workspace]\nmembers = [" + ", ".join(f'"{n}"' for n in names) + "]\n"}
    files["app/Cargo.toml"] = manifest(app_deps).replace('name = "case"', 'name = "app"')
    files["app/src/lib.rs"] = app_src
    for m in members:
        files.update(m)
    return files


SD = 'serde = "1"\nserde_derive = "1"\nshim = { path = "../shim" }\n'


DEPS = 'serde = "1"\nserde_derive = "1"\nclap = { version = "4", features = ["derive"] }\nthiserror = "2"\nshim = { path = "../shim" }\n'
# the sources of each crate's derives, and the library names that capture each
SOURCES = {
    "serde": ("use serde::Serialize;\n#[derive(Serialize)]\n" + S,),
    "clap": ("use clap::Parser;\n#[derive(Parser)]\nstruct Cli;\n",),
    "thiserror": ("use thiserror::Error;\n#[derive(Debug, Error)]\n" + S,),
}
CAPTURES = {"serde": ("serde", "serde_derive", "serde-derive"), "clap": ("clap", "clap_derive", "clap-derive"), "thiserror": ("thiserror", "thiserror_impl", "thiserror-impl")}


@pytest.mark.parametrize("crate, lib_name", [(c, n) for c, names in CAPTURES.items() for n in names])
def test_a_library_of_the_repo_called_like_a_crate_or_macro_crate_of_the_list_refuses_that_crates_derives(make_repo, crate, lib_name):
    """rustc links the repo's library under that name where the real crate is a proc-macro (serde_derive: cargo run printed the shim's
    derive, with `use serde_derive::Serialize;` beside a path dependency whose `[lib] name` is serde_derive). Nothing is trusted through
    serde_derive now, and the facade's own derive is not captured (_derive_trust_lib app4, app5), so this refusal is a second guard."""
    for src in SOURCES[crate]:
        files = workspace(src + "\n", DEPS, member("shim", lib_name), extra_members=["shim"])
        assert verdict(make_repo, files) == REFUSED, src
        [why] = reasons_of(make_repo, files)
        assert "library called" in why, why


@pytest.mark.parametrize("crate", sorted(CAPTURES))
def test_a_library_called_like_another_crate_of_the_list_leaves_this_crates_derives_trusted(make_repo, crate):
    other = next(n for c, names in CAPTURES.items() if c != crate for n in names[:1])
    for src in SOURCES[crate]:
        files = workspace(src + "\n", DEPS, member("shim", other), extra_members=["shim"])
        assert verdict(make_repo, files) == TRUSTED, (other, src)


def test_a_library_called_otherwise_leaves_the_derives_trusted(make_repo):
    files = workspace("use serde::Serialize;\n#[derive(Serialize)]\n" + S, SD, member("shim", "shim_lib"), extra_members=["shim"])
    assert verdict(make_repo, files) == TRUSTED


def test_a_package_with_a_library_whose_name_ripple_cannot_read_refuses(make_repo):
    """`name = 5` is a manifest cargo rejects, and a package ripple cannot read has a library of any name."""
    files = workspace("use serde::Serialize;\n#[derive(Serialize)]\n" + S, SD, member("shim", 5), extra_members=["shim"])
    assert verdict(make_repo, files) == REFUSED


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param('[package\nname = "x"', id="a manifest that is not TOML"),
        pytest.param('[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nx = { package = [] }\n', id="a field of the wrong type"),
    ],
)
def test_a_package_of_the_repo_that_cannot_be_read_may_hold_the_library_so_it_refuses(make_repo, broken):
    members = member("shim", "shim_lib")
    members["shim/Cargo.toml"] = broken
    files = workspace("use serde::Serialize;\n#[derive(Serialize)]\n" + S, SD, members, extra_members=["shim"])
    assert verdict(make_repo, files) == REFUSED
    # the package's own verdict (rs_modules._trust) says it cannot read the libraries. The site's reason may be another guard's: a path
    # dependency that is no package ripple read may be a library called `std`, which the prelude check (#30) names first
    repo = make_repo(files)
    found, _ = walk_languages(repo, [RUST])
    packages = rs_modules.load_workspace(repo, found[RUST.name]).packages.values()
    assert packages and all("cannot read" in (p.trust["serde"] or "") for p in packages), [p.trust["serde"] for p in packages]


def test_the_attribute_macros_are_not_widened_by_it(make_repo):
    """A library called tokio or serial_test is an rlib beside the real rlib (E0464) or a proc macro the real rlib beats: probed, no hole.
    The attribute trust is on main and stays as it was."""
    files = workspace(
        "#[tokio::main]\nasync fn main() {\n    helper();\n}\nfn helper() {}\n",
        'tokio = "1"\nshim = { path = "../shim" }\n',
        member("shim", "tokio"),
        extra_members=["shim"],
    )
    _, graph = read(make_repo, files)
    assert any(e.callee.endswith("helper") for e in graph.edges)


# ---- R2-F1: nothing is trusted through `serde_derive`
#
# rustc links the library a package of the graph calls `serde_derive` (a path dependency, a git one, a registry one, a package in a directory
# ripple does not read) where the real proc-macro crate was meant, and no manifest or lock says what any of them is called. Only the name the
# facade (`serde`) re-exports is safe: it resolves inside serde's own build, against serde's own dependency (the rustc half:
# bench/fixtures/rust-probes/crates/_derive_trust_lib app3 against app4 and app5, and tests/test_rs_derive_trust_probes.py).

SERDE_DERIVE_DEPS = 'serde = "1"\nserde_derive = "1"\n'
THROUGH_SERDE_DERIVE = [
    pytest.param("use serde_derive::Serialize;\n#[derive(Serialize)]\n" + S, id="use serde_derive::Serialize"),
    pytest.param("use serde_derive::Deserialize;\n#[derive(Deserialize)]\n" + S, id="use serde_derive::Deserialize"),
    pytest.param("use ::serde_derive::Serialize;\n#[derive(Serialize)]\n" + S, id="use ::serde_derive::Serialize"),
    pytest.param("use serde_derive::{Serialize};\n#[derive(Serialize)]\n" + S, id="use serde_derive::{Serialize}"),
    pytest.param("use serde_derive::Serialize as Serialize;\n#[derive(Serialize)]\n" + S, id="use serde_derive::Serialize as Serialize"),
    pytest.param("pub fn f() {\n    use serde_derive::Serialize;\n    #[derive(Serialize)]\n    struct T;\n}\n", id="in a block"),
]
WRITTEN_THROUGH_SERDE_DERIVE = [
    pytest.param("#[derive(serde_derive::Serialize)]\n" + S, id="#[derive(serde_derive::Serialize)]"),
    pytest.param("#[derive(::serde_derive::Deserialize)]\n" + S, id="#[derive(::serde_derive::Deserialize)]"),
]


@pytest.mark.parametrize("src", THROUGH_SERDE_DERIVE)
def test_a_derive_imported_from_serde_derive_is_not_trusted_even_where_every_manifest_is_plain(make_repo, src):
    files = lib(src, SERDE_DERIVE_DEPS)
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "is imported as" in why and "serde_derive" in why, why


@pytest.mark.parametrize("src", WRITTEN_THROUGH_SERDE_DERIVE)
def test_a_derive_written_through_serde_derive_is_read_as_any_derive_that_is_not_a_standard_one(make_repo, src):
    files = lib(src, SERDE_DERIVE_DEPS)
    assert verdict(make_repo, files) == REFUSED
    assert reasons_of(make_repo, files) == []  # not a site to prove: the path names no crate of TRUSTED_DERIVES


@pytest.mark.parametrize("crate, src", [("serde", "use serde::{Deserialize, Serialize};\n#[derive(Serialize, Deserialize)]\n" + S), ("serde", "#[derive(serde::Serialize)]\n" + S)])
def test_the_derives_written_through_serde_itself_stay_trusted(make_repo, crate, src):
    """serde re-exports them (`pub use serde_derive::*`) inside its own build, where the proc-macro crate is its own dependency."""
    assert verdict(make_repo, lib(src, SERDE_DERIVE_DEPS)) == TRUSTED


@pytest.mark.parametrize(
    "use, item, deps",
    [
        pytest.param("use clap_derive::Parser;", "#[derive(Parser)]\nstruct Cli;\nfn main() {}\n", CLAP + 'clap_derive = "4"\n', id="clap_derive::Parser"),
        pytest.param("use thiserror_impl::Error;", '#[derive(Debug, Error)]\n#[error("e")]\npub struct E;\n', TE + 'thiserror-impl = "2"\n', id="thiserror_impl::Error"),
    ],
)
def test_no_other_macro_crate_is_a_path_to_a_trusted_derive_either(make_repo, use, item, deps):
    """The macro crates of clap and thiserror were never in TRUSTED_DERIVES: the same capture (a library of the name) applies to them."""
    files = main(f"{use}\n{item}", deps) if "clap" in deps else lib(f"{use}\n{item}", deps)
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "is imported as" in why, why


CAPTURE_MAIN = (
    "use serde_derive::Serialize;\npub trait Tr {\n    fn make() -> &'static str;\n}\n#[derive(Serialize)]\npub struct S;\n"
    'impl Tr for S {\n    fn make() -> &\'static str {\n        "repo trait impl"\n    }\n}\nfn main() {\n    S::make();\n}\n'
)
PM2 = {"pm2/Cargo.toml": '[package]\nname = "pm2"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nproc-macro = true\n', "pm2/src/lib.rs": "// the other derive\n"}


def shim_toml(lib_extra=""):
    return f'[package]\nname = "shim"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "serde_derive"\n{lib_extra}[dependencies]\npm2 = {{ path = "../pm2" }}\n'


def app_files(prefix, shim_dep):
    deps = f'serde = "1"\nserde_derive = "1"\n{shim_dep}'
    return {f"{prefix}app/Cargo.toml": manifest(deps).replace('name = "case"', 'name = "app"'), f"{prefix}app/src/main.rs": CAPTURE_MAIN}


def capture_repos():
    """The four repos of the review, each built by cargo, which printed `inherent from the other derive`: a library called serde_derive that
    ripple does not read as a package. Name -> (files, the directory ripple analyses)."""
    members = '[workspace]\nmembers = ["app", "shim", "pm2"]\nresolver = "2"\n'
    dep = 'shim = { path = "../shim" }\n'
    return {
        # the package's `lib.rs` is in another directory: no source file in `shim/`, so its manifest is never read
        "o2 library elsewhere": ({"Cargo.toml": members, **PM2, "shim/Cargo.toml": shim_toml('path = "../pm2/shim_lib.rs"\n'), "pm2/shim_lib.rs": "pub use pm2::Serialize;\n", **app_files("", dep)}, ""),
        # in a hidden directory the walk skips
        "o3 hidden directory": ({"Cargo.toml": members.replace('"shim"', '".shim"'), **PM2, ".shim/Cargo.toml": shim_toml('path = "../pm2/shim_lib.rs"\n'), "pm2/shim_lib.rs": "pub use pm2::Serialize;\n", **app_files("", 'shim = { path = "../.shim" }\n')}, ""),
        # a path dependency outside the analysed directory
        "o1 outside the directory": ({"Cargo.toml": '[workspace]\nmembers = ["repo/app", "shim", "pm2"]\n', **PM2, "shim/Cargo.toml": shim_toml(), "shim/src/lib.rs": "pub use pm2::Serialize;\n", **app_files("repo/", 'shim = { path = "../../shim" }\n')}, "repo"),
        # a git dependency
        "o4 git dependency": ({**app_files("", 'shim = { git = "https://example.invalid/shim.git" }\n')}, ""),
    }


@pytest.mark.parametrize("name", sorted(capture_repos()))
def test_a_library_called_serde_derive_that_ripple_never_reads_does_not_capture_a_direct_import_into_an_edge(make_repo, name):
    """cargo run printed `inherent from the other derive` for each; the derive is the shim's, so `S::make()` is not the trait impl's."""
    files, sub = capture_repos()[name]
    repo = make_repo(files) / sub
    found, skipped = walk_languages(repo, [RUST])
    index, graph = rs.read_repo(repo, found[RUST.name], skipped)
    assert not [e for e in graph.edges if e.callee.endswith("<S as Tr>::make")], sorted((e.caller, e.callee) for e in graph.edges)
    assert [site.refused for ctx in index.ctxs for site in ctx.derive_sites] and all(site.refused for ctx in index.ctxs for site in ctx.derive_sites)


# ---- L1: a refused site reads as on the base, reasons and their order included

# the sites of one crate, in source order, whose helper attributes the reasons are about
ORDER_TAIL = "pub mod m {\n    struct T;\n    trait Tr {\n        fn make();\n    }\n    impl Tr for T {\n        fn make() {}\n    }\n    fn g() {\n        T::make();\n    }\n}\n"
SER_HEAD = '#[derive(Serialize)]\n#[serde(rename_all = "camelCase")]\npub struct A;\n'
OTHER_HEAD = "#[derive(Foo)]\n#[foo]\npub struct B;\n"
ORDER_CASES = {
    # the reviewer's repo: the refused site comes first, an unknown derive's head second
    "refused first": ("use other::*;\nuse serde::Serialize;\n" + SER_HEAD + OTHER_HEAD + ORDER_TAIL, SERDE + 'other = "1"\n'),
    "refused second": ("use other::*;\nuse serde::Serialize;\n" + OTHER_HEAD + SER_HEAD + ORDER_TAIL, SERDE + 'other = "1"\n'),
    "refused between": (
        "use other::*;\nuse serde::Serialize;\n" + OTHER_HEAD + SER_HEAD + "#[derive(Bar)]\n#[bar]\npub struct C;\n" + ORDER_TAIL,
        SERDE + 'other = "1"\n',
    ),
    "two refused": ("use other::*;\nuse serde::Serialize;\n" + SER_HEAD + SER_HEAD.replace("struct A", "struct A2") + OTHER_HEAD + ORDER_TAIL, SERDE + 'other = "1"\n'),
    "a head of an attribute macro first": ("use other::*;\nuse serde::Serialize;\n#[foo]\npub struct F;\n" + SER_HEAD + OTHER_HEAD + ORDER_TAIL, SERDE + 'other = "1"\n'),
    "in a module before another": (
        "use other::*;\npub mod a {\n    use other::*;\n    use serde::Serialize;\n    #[derive(Serialize)]\n    #[serde(rename)]\n    pub struct A;\n}\n" + OTHER_HEAD + ORDER_TAIL,
        SERDE + 'other = "1"\n',
    ),
}


@pytest.mark.parametrize("name", sorted(ORDER_CASES))
def test_a_refused_site_with_helpers_leaves_the_reasons_in_the_order_the_base_gives_them(make_repo, monkeypatch, name):
    """`refuse_derive` appended the site's helpers to `pending_heads` after every head met in source order, so `impl_taint` (which
    answers `taint[0]`) named another attribute than the base does: same edges, another reason."""
    src, deps = ORDER_CASES[name]
    files = lib(src, deps)
    got = signature(*read(make_repo, files))
    as_before(monkeypatch)
    before = signature(*read(make_repo, files))
    assert got == before
    assert got[6][0] == before[6][0] and got[6][0], got[6]  # the taint, in order, and not vacuously empty


def test_the_order_repo_of_the_review_reads_the_serde_attribute_first(make_repo):
    src, deps = ORDER_CASES["refused first"]
    _, graph = read(make_repo, lib(src, deps))
    [reason] = {u.reason for u in graph.unresolved}
    assert "`#[serde]`" in reason, reason

