"""The rules behind the trusted derives (tests/test_rs_derive_trust.py has the identity cases): each of the seven derives is trusted,
and each narrowing and refusal the rules name has a test that fails without its guard.

  * serde's named const `_IMPL_<SERIALIZE|DESERIALIZE>_FOR_<T>` (serde_derive up to 1.0.162) is a name in the scope: a placeholder def
    stands for it, so no call that could be the const's is read as another item's, whatever version builds;
  * `#[serde(remote = "..")]` gives the type an inherent `serialize` or `deserialize` that beats a repo trait's: DERIVE_FNS has both;
  * a serde_derive that runs a precompiled binary (1.0.172-alpha.0 to 1.0.183) is not trusted when the lock lists it, and is when there
    is no lock to say (as the attribute macros are);
  * `#[serde(crate = "..")]` refuses the site, also under cfg_attr;
  * clap's `clap::..` and thiserror's `std::..` paths are resolved where the derive is written: `clap` is the dependency and `std` is
    the standard library there, or the site is refused;
  * `use serde::Serialize;` and `use clap::Parser;` give the derive only when the crate's `derive` feature is on, so no glob may be
    able to give the name in the macro namespace instead (the importing scope and the blocks around it); thiserror's import carries the
    derive always. Nothing is trusted through `serde_derive` (tests/test_rs_derive_trust_review.py, R2-F1);
  * a repo that is serde, clap or thiserror is never trusted;
  * a bare attribute beside a trusted derive is its helper when the derive declares it in every version: the declared helper beats an
    imported attribute macro of the name (bench/fixtures/rust-probes/crates/_derive_trust, built by rustc).
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402
from test_rs_derive_trust import CIO, CLAP, REFUSED, S, SERDE, TE, TRUSTED, as_before, lib, lock, main, manifest, read, reasons_of, signature, verdict  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

DATA = FIXTURES / "rust-probes" / "derive_trust"
SD = 'serde = "1"\nserde_derive = "1"\n'
PROBE = "mod probe_mod {\n    pub fn one() {}\n}\nuse probe_mod::*;\nfn f() {\n    one();\n    let _ = String::new();\n}\n"
# a name found through the prelude, and a function of a trait impl of a type of the crate: both unresolved where a macro may have made names or impls
HERE = "struct T;\ntrait Tr {\n    fn make();\n}\nimpl Tr for T {\n    fn make() {}\n}\nfn g() {\n    let _ = String::new();\n    T::make();\n}\n"


def calls(make_repo, files):
    _, graph = read(make_repo, files)
    return sorted((e.caller, e.callee) for e in graph.edges), sorted((u.caller, u.text, u.reason) for u in graph.unresolved)


# ---- the seven derives


@pytest.mark.parametrize(
    "derive, deps, uses, item",
    [
        pytest.param("Serialize", SERDE, "use serde::Serialize;", S, id="Serialize through serde"),
        pytest.param("Deserialize", SERDE, "use serde::Deserialize;", S, id="Deserialize through serde"),
        pytest.param("Parser", CLAP, "use clap::Parser;", S, id="clap Parser"),
        pytest.param("Args", CLAP, "use clap::Args;", S, id="clap Args"),
        pytest.param("Subcommand", CLAP, "use clap::Subcommand;", "pub enum S {\n    A,\n}\n", id="clap Subcommand on an enum"),
        pytest.param("ValueEnum", CLAP, "use clap::ValueEnum;", "pub enum S {\n    A,\n}\n", id="clap ValueEnum on an enum"),
        pytest.param("Error", TE, "use thiserror::Error;", S, id="thiserror Error"),
    ],
)
def test_each_of_the_seven_derives_leaves_its_scope_and_its_impls_alone_where_the_repo_proves_it(make_repo, derive, deps, uses, item):
    """`String::new()` (found through the prelude) is an external call and `T::make()` (a trait impl of a type of the crate) an edge:
    under a derive that is not a standard one both are unresolved (the next test)."""
    files = lib(uses + "\n#[derive(" + derive + ")]\n" + item + HERE, deps)
    edges, unresolved = calls(make_repo, files)
    assert unresolved == [], unresolved
    assert sorted(c for _, c in edges) == ["src/lib:<T as Tr>::make"], edges


@pytest.mark.parametrize(
    "derive, deps, uses",
    [
        ("Serialize", SERDE, "use serde::Serialize;"),
        ("Parser", CLAP, "use clap::Parser;"),
        ("Error", TE, "use thiserror::Error;"),
    ],
)
def test_the_same_repo_under_the_old_rule_has_those_calls_unresolved(make_repo, monkeypatch, derive, deps, uses):
    files = lib(uses + "\n#[derive(" + derive + ")]\n" + S + HERE, deps)
    as_before(monkeypatch)
    _, unresolved = calls(make_repo, files)
    assert sorted(text for _, text, _ in unresolved) == ["String::new", "T::make"]
    assert {reason.split(":")[0] for *_, reason in unresolved} == {rs.MACRO_MAY_MAKE, rs.IMPLS_INCOMPLETE.split(":")[0]}


def test_a_derive_under_a_cfg_attr_is_trusted_as_one_written_plain(make_repo):
    files = lib('use serde::Serialize;\n#[cfg_attr(feature = "serde", derive(Serialize))]\n' + S)
    assert verdict(make_repo, files) == TRUSTED


def test_a_derive_beside_standard_derives_is_trusted_and_they_keep_their_own_proof(make_repo):
    files = lib("use serde::Serialize;\n#[derive(Debug, Clone, Serialize, PartialEq)]\n" + S)
    assert verdict(make_repo, files) == TRUSTED


# ---- serde's old named const


def named_fn(derive, other="S"):
    """A function of the name the old const had, in the module around a block that holds the derive: the const (a name of the block's own)
    shadows it there, and the anonymous const of a later version does not. (A glob beside the import would refuse the serde derive, the
    `derive` feature; an import of the name by `use` clashes with the const, E0255, and so builds in no version that writes it.)"""
    return f"#[allow(non_snake_case)]\nfn _IMPL_{derive.upper()}_FOR_{other}() -> u8 {{\n    1\n}}\n"


def in_block(src, call="_IMPL_SERIALIZE_FOR_S"):
    body = "".join(f"    {line}\n" for line in src.splitlines())
    return f"fn f() {{\n{body}    {call}();\n}}\n"


@pytest.mark.parametrize("derive", ["Serialize", "Deserialize"])
def test_a_call_that_could_be_serdes_old_named_const_is_not_the_function_of_the_module_around(make_repo, derive):
    """serde_derive up to 1.0.162 writes `const _IMPL_SERIALIZE_FOR_S: () = ..` beside the type, which shadows a function of that name in
    the module around the block (rustc: the call is then E0618, so only the version that is built tells which). The name is read as one a
    macro made."""
    files = lib(named_fn(derive) + in_block(f"use serde::{derive};\n#[derive({derive})]\nstruct S;", f"_IMPL_{derive.upper()}_FOR_S"), SERDE)
    edges, unresolved = calls(make_repo, files)
    assert edges == [] and unresolved == [("src/lib:f", f"_IMPL_{derive.upper()}_FOR_S", rs.ATTR_MACRO)]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param(in_block("#[derive(Debug)]\nstruct S;"), id="a standard derive writes no such name"),
        pytest.param(in_block("use serde::Serialize;\n#[derive(Serialize)]\nstruct T;"), id="the name of another type"),
        pytest.param(in_block("use serde::Deserialize;\n#[derive(Deserialize)]\nstruct S;"), id="the other derive's name"),
    ],
)
def test_the_placeholder_stands_for_the_name_of_the_derive_and_the_type_only(make_repo, src):
    edges, unresolved = calls(make_repo, lib(named_fn("Serialize") + src, SERDE))
    assert unresolved == [] and edges == [("src/lib:f", "src/lib:_IMPL_SERIALIZE_FOR_S")]


def test_the_placeholder_of_a_refused_site_is_gone_as_it_never_was(make_repo, monkeypatch):
    files = lib(named_fn("Serialize") + in_block("use fake::Serialize;\nuse serde::Serialize;\n#[derive(Serialize)]\nstruct S;"), SERDE + 'fake = "1"\n')
    got = signature(*read(make_repo, files))
    assert reasons_of(make_repo, files) != [None]
    as_before(monkeypatch)
    assert got == signature(*read(make_repo, files))


# ---- #[serde(remote = "..")]


@pytest.mark.parametrize("derive, fn", [("Serialize", "serialize"), ("Deserialize", "deserialize")])
def test_remote_gives_the_type_an_inherent_function_that_beats_a_repo_trait_impl_of_the_name(make_repo, derive, fn):
    """MEASURED (bench/fixtures/rust-probes/crates/_derive_trust, p2): `Foo::serialize()` prints the derive's own, over the trait impl.
    DERIVE_FNS has the name for each derive, so the call is both a derive's and an impl's, not the impl's."""
    files = lib(
        f"use serde::{derive};\ntrait T {{\n    fn {fn}() -> u8;\n}}\n#[derive({derive})]\n#[serde(remote = \"Other\")]\n" + S + f"impl T for S {{\n    fn {fn}() -> u8 {{\n        1\n    }}\n}}\nfn f() {{\n    S::{fn}();\n}}\n"
    )
    assert verdict(make_repo, files) == TRUSTED
    edges, unresolved = calls(make_repo, files)
    assert edges == [] and unresolved == [("src/lib:f", f"S::{fn}", rs.DERIVE_AND_IMPL)]


def test_the_derive_functions_of_serde_are_the_two_remote_adds():
    assert rs.DERIVE_FNS["Serialize"] == {"serialize"} and rs.DERIVE_FNS["Deserialize"] == {"deserialize"}


# ---- serde_derive's precompiled binary


def serde_lock(version, serde="1.0.228"):
    return {"Cargo.lock": lock(("serde", serde, CIO), ("serde_derive", version, CIO))}


def precompiled_versions():
    versions = (DATA / "serde_derive.versions.txt").read_text().split()
    first, last = versions.index("1.0.172-alpha.0"), versions.index("1.0.183")
    return versions[first : last + 1]


def test_the_thirteen_precompiled_versions_are_read_from_the_index_listing_not_a_range_ripple_guesses():
    """The crates.io index lists these 13 versions between 1.0.172-alpha.0 and 1.0.183 (all that ship lib_precompiled.rs)."""
    listed = precompiled_versions()
    assert len(listed) == 13 and listed[0] == "1.0.172-alpha.0" and listed[-1] == "1.0.183"
    assert rs.PRECOMPILED_SERDE_DERIVE == frozenset(listed)
    everything = (DATA / "serde_derive.versions.txt").read_text().split()
    assert len(everything) == 271 and everything[everything.index("1.0.172-alpha.0") - 1] == "1.0.171" and everything[everything.index("1.0.183") + 1] == "1.0.184"


@pytest.mark.parametrize("version", precompiled_versions())
def test_a_lock_that_lists_a_precompiled_serde_derive_refuses_the_serde_derives(make_repo, version):
    files = lib("use serde::{Deserialize, Serialize};\n#[derive(Serialize, Deserialize)]\n" + S, files=serde_lock(version))
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "precompiled binary" in why, why


@pytest.mark.parametrize("version", ["1.0.171", "1.0.184", "1.0.162", "1.0.229", "1.0.0", "1.0.172-alpha"])
def test_a_serde_derive_next_to_the_precompiled_ones_is_trusted(make_repo, version):
    assert verdict(make_repo, lib(SER_USE + S, files=serde_lock(version))) == TRUSTED


SER_USE = "use serde::Serialize;\n#[derive(Serialize)]\n"


def test_with_no_lock_the_manifests_alone_decide_as_they_do_for_the_attribute_macros(make_repo):
    assert verdict(make_repo, lib(SER_USE + S)) == TRUSTED


def test_one_precompiled_version_among_several_in_the_lock_refuses(make_repo):
    """A lock may hold several serde_derive (one per semver-incompatible request): which one this package builds is not known."""
    files = lib(SER_USE + S, files={"Cargo.lock": lock(("serde", "1.0.228", CIO), ("serde_derive", "1.0.228", CIO), ("serde_derive", "1.0.180", CIO))})
    assert verdict(make_repo, files) == REFUSED


def test_a_precompiled_serde_derive_in_the_lock_does_not_touch_the_other_two_crates(make_repo):
    files = main("use clap::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n") | {"Cargo.lock": lock(("clap", "4.6.7", CIO), ("clap_derive", "4.6.7", CIO), ("serde_derive", "1.0.180", CIO))}
    assert verdict(make_repo, files) == TRUSTED


# ---- #[serde(crate = "..")]


@pytest.mark.parametrize(
    "attr",
    [
        '#[serde(crate = "self::s")]',
        '#[serde(rename_all = "camelCase", crate = "self::s")]',
        '#[serde(bound(serialize = "T: Copy"), crate = "self::s")]',
        '#[cfg_attr(feature = "x", serde(crate = "self::s"))]',
        '#[cfg_attr(feature = "x", derive(Debug), serde(crate = "self::s"))]',
        '#[cfg_attr(a, cfg_attr(b, serde(crate = "self::s")))]',
        '#[serde(\n    crate\n    =\n    "self::s"\n)]',
    ],
)
def test_a_serde_crate_attribute_refuses_the_site_whatever_it_is_written_in(make_repo, attr):
    files = lib("use serde::{Deserialize, Serialize};\n#[derive(Deserialize)]\n" + attr + "\n" + S)
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "#[serde(crate = ..)]" in why


@pytest.mark.parametrize("attr", ['#[serde(rename = "crate")]', '#[serde(rename_all = "camelCase")]', "#[serde(default)]", '#[serde(with = "crate_x")]', '#[serde(remote = "Other")]'])
def test_any_other_serde_attribute_leaves_the_site_trusted(make_repo, attr):
    assert verdict(make_repo, lib("use serde::Serialize;\n#[derive(Serialize)]\n" + attr + "\n" + S)) == TRUSTED


# ---- clap's paths


@pytest.mark.parametrize("derive", ["Parser", "Args", "Subcommand", "ValueEnum"])
@pytest.mark.parametrize(
    "shadow",
    [
        pytest.param("mod clap {\n    pub trait Tr {}\n}\n", id="a module"),
        pytest.param("use fake as clap;\n", id="an alias"),
        pytest.param("extern crate fake as clap;\n", id="an extern crate alias"),
        pytest.param("struct clap;\n", id="a unit struct"),
    ],
)
def test_clap_must_be_the_dependency_where_any_clap_derive_is_written(make_repo, derive, shadow):
    """MEASURED (_derive_trust/p4): `impl clap::Tr for S` written by the derive implements the trait of a local `mod clap`."""
    files = main(f"use ::clap::{derive};\n{shadow}#[derive({derive})]\nstruct Cli;\nfn main() {{}}\n", CLAP + 'fake = "1"\n')
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "`clap` here is" in why, why


def test_the_clap_check_is_made_where_the_derive_is_written_not_where_the_name_is_imported(make_repo):
    files = main("use clap::Parser;\nfn f() {\n    mod clap {\n        pub trait Tr {}\n    }\n    #[derive(Parser)]\n    struct Cli;\n}\nfn main() {}\n")
    assert verdict(make_repo, files) == REFUSED


def test_a_clap_in_another_module_is_no_matter(make_repo):
    files = main("mod other {\n    pub mod clap {}\n}\nuse clap::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n")
    assert verdict(make_repo, files) == TRUSTED


def test_serdes_and_thiserrors_names_are_not_asked_of_clap(make_repo):
    """Only clap's derives write `clap::`: a module called clap beside a serde derive is no matter."""
    assert verdict(make_repo, lib("mod clap {}\n" + SER_USE + S)) == TRUSTED


# ---- thiserror's paths


def derive_error(prelude=""):
    return prelude + 'use thiserror::Error;\n#[derive(Debug, Error)]\n#[error("e")]\npub struct E;\n'


@pytest.mark.parametrize(
    "prelude",
    [
        pytest.param("mod std {\n    pub mod error {\n        pub trait Error {}\n    }\n}\n", id="a module"),
        pytest.param("use fake as std;\n", id="an alias"),
        pytest.param("extern crate fake as std;\n", id="an extern crate alias"),
        pytest.param("mod m {\n    pub mod std {}\n}\nuse m::std;\n", id="an import of another module"),
    ],
)
def test_std_must_be_the_standard_library_where_a_thiserror_derive_is_written(make_repo, prelude):
    """MEASURED (_derive_trust/p5): `impl std::fmt::Display for S` written by the derive implements the trait of a local `mod std`."""
    files = lib(derive_error(prelude), TE + 'fake = "1"\n')
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "`std` here is" in why, why


STD_DEP = {"Cargo.toml": '[workspace]\nmembers = ["app", "shim"]\n', "shim/Cargo.toml": '[package]\nname = "shim"\nversion = "0.1.0"\nedition = "2021"\n[lib]\nname = "std"\n', "shim/src/lib.rs": ""}


def app(extra_deps=""):
    return {"app/Cargo.toml": manifest(TE + extra_deps).replace('name = "case"', 'name = "app"'), "app/src/lib.rs": derive_error()}


@pytest.mark.parametrize(
    "files, how",
    [
        pytest.param(STD_DEP | app('shim = { path = "../shim" }\n'), "a dependency whose library is called std", id="a dependency of another key whose library is called std"),
        pytest.param({"Cargo.toml": manifest(TE) + '\n[lib]\nname = "std"\n', "src/lib.rs": derive_error()}, "the package's own library is called std", id="the package's own library is called std"),
        pytest.param({"Cargo.toml": manifest(TE + 'core = "1"\n'), "src/lib.rs": derive_error()}, "a dependency keyed core", id="a dependency keyed core"),
    ],
)
def test_a_crate_whose_std_may_be_another_crates_is_refused_by_the_test_the_standard_derives_use(make_repo, files, how):
    """The extern prelude of such a crate gives `std` to the other crate, which the lookup of the name in the scope does not see; the
    standard derives' replacement (Index.prelude_may_not_be_std) asks the same, so the two cannot disagree."""
    index, _ = read(make_repo, files)
    sites = [(ctx, site) for ctx in index.ctxs for site in ctx.derive_sites]
    assert sites and all(index.prelude_may_not_be_std(ctx) for ctx, _ in sites), how
    # refused by the prelude check of the `derive` attribute itself (Index.shadow_problem, #30), which comes before the expansion's own `std` check
    assert all(site.refused is not None and ("std" in site.refused or "standard prelude" in site.refused) for _, site in sites), [site.refused for _, site in sites]


FAMILIES = [  # (crate, file, source, its dependency line): one derive of each crate
    ("serde", "src/lib.rs", SER_USE + S, SERDE),
    ("clap", "src/main.rs", "use clap::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n", CLAP),
    ("thiserror", "src/lib.rs", derive_error(), TE),
]


@pytest.mark.parametrize(
    "way, deps, extra",
    [
        pytest.param("a dependency keyed core", 'core = "1"\n', "", id="core"),
        pytest.param("a dependency keyed std", 'std = "1"\n', "", id="std"),
        pytest.param("the package's own library called std", "", '\n[lib]\nname = "std"\n', id="own-lib"),
    ],
)
@pytest.mark.parametrize("crate, path, src, crate_deps", FAMILIES, ids=[f[0] for f in FAMILIES])
def test_a_crate_whose_std_or_core_may_be_another_crates_refuses_every_trusted_derive(make_repo, crate, path, src, crate_deps, way, deps, extra):
    """Each rule of the three leans on a name the prelude or the extern prelude gives (thiserror's `std::`, clap's `clap::`, the `derive`
    attribute of all of them), and #30 made a replaced prelude unknown at every derive. Control: without it the same derive is trusted."""
    refused = {"Cargo.toml": manifest(crate_deps + deps, extra=extra), path: src}
    index, _ = read(make_repo, refused)
    assert all(index.prelude_may_not_be_std(ctx) for ctx in index.ctxs if ctx.derive_sites), way
    assert verdict(make_repo, refused) == REFUSED
    [why] = [site.refused for ctx in index.ctxs for site in ctx.derive_sites]
    assert why is not None and ("std" in why or "standard prelude" in why), why


@pytest.mark.parametrize("crate, path, src, crate_deps", FAMILIES, ids=[f[0] for f in FAMILIES])
def test_the_same_derives_with_a_prelude_that_is_the_standard_ones_are_trusted(make_repo, crate, path, src, crate_deps):
    assert verdict(make_repo, {"Cargo.toml": manifest(crate_deps), path: src}) == TRUSTED


def test_a_std_that_may_be_another_crates_refuses_the_derives_of_serde_and_clap_too(make_repo):
    """Neither expands to a `std::..` path, but `derive` itself is a macro of the prelude: a `core` that may be another crate may give another
    one (Index.shadow_problem), so no derive written under it is proven the published crate's."""
    files = lib(SER_USE + S, SERDE + 'core = "1"\n')
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "standard prelude" in why and "called derive" in why, why


# ---- the macro namespace when the derive is behind a feature


OTHER_GLOB = "mod other_mod {\n    pub fn one() {}\n}\nuse other_mod::*;\n"


@pytest.mark.parametrize(
    "crate, deps, uses, item",
    [
        ("serde", SERDE, "use serde::Serialize;", "#[derive(Serialize)]\n" + S),
        ("clap", CLAP, "use clap::Parser;", "#[derive(Parser)]\nstruct Cli;\n"),
    ],
)
@pytest.mark.parametrize(
    "where_, template",
    [
        pytest.param("module", "{uses}\n{glob}{item}", id="a glob in the importing module"),
        pytest.param("block", "{uses}\nfn f() {{\n    {glob}    {item}}}\n", id="a glob in the function whose block holds the derive"),
        pytest.param("module around", "{glob}fn f() {{\n    {uses}\n    {item}}}\n", id="a glob in the module around the block that imports"),
        pytest.param("block around", "fn f() {{\n    {glob}    {{\n        {uses}\n        {item}    }}\n}}\n", id="a glob in a block around the block that imports"),
    ],
)
def test_a_feature_gated_import_is_refused_when_a_glob_around_it_could_give_the_name_instead(make_repo, crate, deps, uses, item, where_, template):
    """MEASURED (_derive_trust/p6): with the `derive` feature off `use serde::Serialize;` gives the trait, and the macro namespace goes
    on to the glob, which gives another derive of the name. The feature is not proof: the manifest may be for another target."""
    src = template.format(uses=uses, glob=OTHER_GLOB.replace("\n", "\n    ") if "block" in where_ else OTHER_GLOB, item=item)
    files = {**(lib(src, deps) if crate == "serde" else main(src + "fn main() {}\n", deps))}
    assert verdict(make_repo, files) == REFUSED
    assert any("glob" in (why or "") or "`" in (why or "") for why in reasons_of(make_repo, files))


def test_thiserrors_import_carries_the_derive_always_so_a_glob_beside_it_is_no_matter(make_repo):
    item = '#[derive(Debug, Error)]\n#[error("e")]\npub struct E;\n'
    assert verdict(make_repo, lib(f"use thiserror::Error;\n{OTHER_GLOB}{item}", TE)) == TRUSTED


def test_a_glob_of_a_parent_module_is_not_around_the_import(make_repo):
    """A module sees its own names: the parent's glob reaches it by neither the import nor the macro namespace."""
    files = lib(OTHER_GLOB + "mod m {\n    use serde::Serialize;\n    #[derive(Serialize)]\n    pub struct S;\n}\n")
    assert verdict(make_repo, files) == TRUSTED


def test_the_derive_feature_in_the_manifest_is_not_what_proves_the_name(make_repo):
    """The rule is the glob's absence, not a `features = [\"derive\"]` ripple would have to prove for every target and every
    dependency table: a manifest that has it still refuses beside a glob, and one that has not trusts without one."""
    assert verdict(make_repo, lib("use serde::Serialize;\n" + OTHER_GLOB + "#[derive(Serialize)]\n" + S, SERDE)) == REFUSED
    assert verdict(make_repo, lib(SER_USE + S, 'serde = "1"\n')) == TRUSTED


# ---- the repo is the crate


@pytest.mark.parametrize(
    "package, deps, use, item",
    [
        ("serde", SERDE, "use serde::Serialize;", "#[derive(Serialize)]\n" + S),
        ("serde_derive", SD, "use serde::Serialize;", "#[derive(Serialize)]\n" + S),
        ("clap", CLAP, "use clap::Parser;", "#[derive(Parser)]\nstruct Cli;\n"),
        ("clap_derive", CLAP, "use clap::Parser;", "#[derive(Parser)]\nstruct Cli;\n"),
        ("thiserror", TE, "use thiserror::Error;", '#[derive(Debug, Error)]\n#[error("e")]\npub struct E;\n'),
        ("thiserror-impl", TE, "use thiserror::Error;", '#[derive(Debug, Error)]\n#[error("e")]\npub struct E;\n'),
    ],
)
def test_a_repo_with_a_package_called_like_the_crate_or_its_macro_crate_is_never_trusted(make_repo, package, deps, use, item):
    """The serde and clap corpora are the crates themselves, whose derives are the repo's own proc-macro crates."""
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
        "a/Cargo.toml": manifest(deps).replace('name = "case"', 'name = "a"'),
        "a/src/lib.rs": f"{use}\n{item}",
        "b/Cargo.toml": manifest("").replace('name = "case"', f'name = "{package}"'),
        "b/src/lib.rs": "",
    }
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "a package of the repo is called" in why, why


# ---- what shapes the site


@pytest.mark.parametrize(
    "src, why",
    [
        pytest.param("#[tokio::main]\nasync fn main() {\n    use serde::Serialize;\n    #[derive(Serialize)]\n    struct S;\n    let _ = String::new();\n}\n", "", id="written by a use"),
        pytest.param("#[tokio::main]\nasync fn main() {\n    #[derive(::serde::Serialize)]\n    struct S;\n    let _ = String::new();\n}\n", "inside a refused one", id="written by a global path, which no scope lookup refuses"),
    ],
)
def test_a_derive_inside_an_item_a_refused_attribute_may_have_rewritten_reads_as_the_derive_did_before(make_repo, monkeypatch, src, why):
    """`#[tokio::main]` is trusted until its dependency is shown not to be crates.io's: the struct in its body is then in an item that
    is rewritten, where a derive is read as any derive is."""
    files = {"Cargo.toml": manifest('tokio = { path = "../tokio" }\nserde = "1"\n'), "src/main.rs": src}
    got = signature(*read(make_repo, files))
    [refused] = reasons_of(make_repo, files)
    assert refused is not None and why in refused, refused
    as_before(monkeypatch)
    assert got == signature(*read(make_repo, files))


def test_a_derive_inside_an_item_under_an_attribute_macro_reads_as_the_derive_did_before(make_repo, monkeypatch):
    """The body of a function under an attribute macro that is not trusted is read as rewritten when it is collected, so a derive in it
    is never queued, written however."""
    files = {
        "Cargo.toml": manifest('other = "1"\nserde = "1"\n'),
        "src/main.rs": "#[other::attr]\nfn main() {\n    #[derive(::serde::Serialize)]\n    struct S;\n    let _ = String::new();\n}\n",
    }
    got = signature(*read(make_repo, files))
    assert reasons_of(make_repo, files) == []
    as_before(monkeypatch)
    assert got == signature(*read(make_repo, files))


def test_a_global_path_names_the_extern_prelude_whatever_a_root_extern_crate_says(make_repo):
    """`extern crate fake as serde;` in the root puts that crate in the extern prelude under the name, though the manifest depends on serde
    plainly: `::serde::Serialize` is then not serde's."""
    files = lib("extern crate fake as serde;\n#[derive(::serde::Serialize)]\n" + S, SERDE + 'fake = "1"\n')
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert "`::serde` is" in why, why


def test_edition_2015_refuses_a_derive_written_by_a_global_path_too(make_repo):
    """In a 2015 crate `::serde::Serialize` is a path from the crate root, not the extern prelude: no scope lookup says so, the edition does."""
    files = lib("extern crate serde;\n#[derive(::serde::Serialize)]\n" + S, edition="2015")
    assert verdict(make_repo, files) == REFUSED
    [why] = reasons_of(make_repo, files)
    assert why.startswith("edition"), why


@pytest.mark.parametrize("item", ["fn f() {}", "mod m {}", "trait Tr {}", "const C: u8 = 1;"])
def test_a_derive_written_on_what_is_no_type_is_read_as_it_always_was(make_repo, monkeypatch, item):
    files = lib("use serde::Serialize;\n#[derive(Serialize)]\n" + item + "\nfn g() {\n    let _ = String::new();\n}\n")
    got = signature(*read(make_repo, files))
    assert reasons_of(make_repo, files) == []
    as_before(monkeypatch)
    assert got == signature(*read(make_repo, files))


@pytest.mark.parametrize("item", ["pub struct S;", "pub struct S {\n    a: u8,\n}", "pub struct S(u8);", "pub enum S {\n    A,\n}"])
def test_a_derive_is_trusted_on_a_struct_of_any_shape_and_on_an_enum(make_repo, item):
    assert verdict(make_repo, lib("use serde::Serialize;\n#[derive(Serialize)]\n" + item + "\n")) == TRUSTED


def test_use_thiserror_error_beside_an_enum_called_error_costs_two_kinds_of_calls_a_known_conservative_loss(make_repo):
    """Not wrong, and not undone here (a macro-only namespace for `use thiserror::Error;` is not read): `thiserror::Error` is a macro and
    no type, but ripple counts an import in every namespace, so `Error` is the enum and an item of the crate. `Error::Msg` is then
    several definitions, and the impl `From<..> for Error` may be for a type from outside the repo, which makes every `X::from` a call
    that a repo trait may answer. bat has four of the second kind (`PathBuf::from`, `String::from`, `u32::from`) and two of the first.
    It flips to edges and externals when the macro-only namespace is read."""
    files = lib(
        'use thiserror::Error;\n#[derive(Debug, Error)]\npub enum Error {\n    #[error("a")]\n    A,\n    #[error("m")]\n    Msg(String),\n}\n'
        "impl From<std::io::Error> for Error {\n    fn from(_: std::io::Error) -> Self {\n        Error::A\n    }\n}\n"
        'pub fn f() {\n    let _ = String::from("x");\n    let _ = Error::Msg(String::new());\n    let _ = Vec::<u8>::new();\n}\n',
        TE,
    )
    assert verdict(make_repo, files) == TRUSTED
    edges, unresolved = calls(make_repo, files)
    assert edges == []
    assert sorted((text, reason) for _, text, reason in unresolved) == [("Error::Msg", rs.SEVERAL), ("String::from", rs.EXT_TRAIT_SHADOW)]


def test_a_derive_that_is_not_trusted_beside_one_that_is_refuses_both(make_repo):
    """Another derive, or an attribute macro, may generate any item beside the type: the item is read as under any such derive."""
    for beside in ("#[derive(Serialize, other::Thing)]", "#[derive(Serialize)]\n#[other::attr]", "#[derive(Serialize)]\n#[frobnicate]"):
        files = lib("use serde::Serialize;\n" + beside + "\n" + S, SERDE + 'other = "1"\n')
        assert verdict(make_repo, files) == REFUSED, beside
        assert reasons_of(make_repo, files) == [], beside


def test_a_trusted_derive_does_not_hide_the_marks_of_a_site_that_is_refused_in_the_same_scope(make_repo):
    files = lib("use serde::Serialize;\nuse fake::Serialize;\n#[derive(Serialize)]\npub struct A;\n#[derive(Debug, other::Thing)]\npub struct B;\n", SERDE + 'fake = "1"\nother = "1"\n')
    assert verdict(make_repo, files) == REFUSED


# ---- helpers beside a trusted derive (the version-independent ones)


@pytest.mark.parametrize(
    "derive, deps, uses, helper",
    [
        ("Serialize", SERDE, "use serde::Serialize;", '#[serde(rename_all = "camelCase")]'),
        ("Parser", CLAP, "use clap::Parser;", "#[clap(author)]"),
        ("Args", CLAP, "use clap::Args;", "#[clap(flatten)]"),
        ("Error", TE, "use thiserror::Error;", '#[error("e")]'),
        ("Error", TE, "use thiserror::Error;", "#[error(transparent)]"),
    ],
)
@pytest.mark.parametrize("elsewhere", ["", "mod m {\n    use other::{serde, clap, error};\n}\n", "fn elsewhere() {\n    use other::{serde, clap, error};\n}\n"])
def test_a_helper_the_derive_declares_in_every_version_is_its_helper_whatever_attribute_macro_is_imported_by_the_name(make_repo, derive, deps, uses, helper, elsewhere):
    """MEASURED (_derive_trust/p3a, p3b): a helper written after the derive that declares it beats an attribute macro of the same name
    in scope, in the same module or another. Today's rule marks the crate for every use of the name."""
    files = lib(f"{uses}\n{elsewhere}#[derive({derive})]\n{helper}\npub struct S;\n", deps + 'other = "1"\n')
    assert verdict(make_repo, files) == TRUSTED


def test_a_helper_beats_an_attribute_macro_imported_in_the_same_module(make_repo):
    """MEASURED (_derive_trust/p3a): `use pm::command;` beside `#[derive(D)] #[command]` builds, with the helper."""
    files = lib(derive_error("use other::error;\n"), TE + 'other = "1"\n')
    assert verdict(make_repo, files) == TRUSTED


def test_a_helper_of_a_refused_site_is_read_as_it_always_was(make_repo, monkeypatch):
    """The helpers are taken on the derive's word, so a refusal puts them back: the name is an attribute macro where a `use` gives it."""
    files = lib('use other::serde;\n#[derive(serde::Serialize)]\n#[serde(rename_all = "x")]\n' + S + PROBE, 'serde = "1"\nother = "1"\n')
    got = signature(*read(make_repo, files))
    assert reasons_of(make_repo, files) != [None]
    as_before(monkeypatch)
    assert got == signature(*read(make_repo, files))
