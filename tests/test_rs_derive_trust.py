"""The derives ripple trusts: serde's `Serialize` and `Deserialize`, clap's `Parser`, `Args`, `Subcommand` and `ValueEnum`, and thiserror's `Error`.

Any derive that is not the standard library's marks its scope (a macro there may have made names beside the type) and the impls
of the crate it is in (it may have added one): sound, and costly where the derive is one of these seven, which every published version
of the three crates was read for (README, "Derives ripple trusts"). What each expands to, in every version: trait impls of its own
crate's traits for the deriving type, nothing else, with three exceptions that are narrowed here:

  * serde_derive up to 1.0.162 may write a named const `_IMPL_SERIALIZE_FOR_<T>` beside the type: a placeholder def stands for it;
  * `#[serde(remote = "..")]` adds an inherent `serialize` or `deserialize`, which DERIVE_FNS already covers;
  * `#[serde(crate = "..")]` makes the impls those of a path that may be a repo module: refused.

And two paths the expansion writes where the derive is written (`clap::..` from clap 3, `std::..` from thiserror 1) which a local `mod`
can capture: the name must be the dependency, the standard library, at the site. The trust is the repo's to prove, as for the three
attribute macros (tests/test_rs_attr_trust.py): the manifests, the lock, the config files, and how the derive's name is written here.
A site the repo does not prove is read exactly as the same derive was read before the rule: the same flags, the same reasons.

The sites below are the adversarial identity cases of the investigation (38 repos), each trusted or refused, plus one test for every
guard that has none of its own.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

SERDE = 'serde = { version = "1", features = ["derive"] }\n'
CLAP = 'clap = { version = "4", features = ["derive"] }\n'
TE = 'thiserror = "2"\n'
CIO = 'source = "registry+https://github.com/rust-lang/crates.io-index"'
TRUSTED, REFUSED = "trusted", "refused"


def manifest(deps, edition="2021", extra=""):
    return f'[package]\nname = "case"\nversion = "0.1.0"\nedition = "{edition}"\n\n[dependencies]\n{deps}{extra}'


def lock(*pkgs):
    body = "version = 3\n\n" + "".join(f'[[package]]\nname = "{n}"\nversion = "{v}"\n{src}\n' for n, v, src in pkgs)
    return body + '[[package]]\nname = "case"\nversion = "0.1.0"\n'


def read(make_repo, files):
    repo = make_repo(files)
    found, skipped = walk_languages(repo, [RUST])
    return rs.read_repo(repo, found[RUST.name], skipped)


def marked(index):
    return any(scope.attribute_macro for ctx in index.ctxs for scope in index.all_scopes(ctx))


def verdict(make_repo, files):
    """TRUSTED when no scope of the repo is read as one a derive may have made names in, and no derive is a reason the impls may be incomplete."""
    index, _ = read(make_repo, files)
    if marked(index):
        return REFUSED
    assert [r for ctx in index.ctxs for r in ctx.attr_reasons()] == []
    return TRUSTED


def lib(src, deps=SERDE, files=None, **kw):
    return {"Cargo.toml": manifest(deps, **kw), "src/lib.rs": src, **(files or {})}


def main(src, deps=CLAP):
    return {"Cargo.toml": manifest(deps), "src/main.rs": src}


S = "pub struct S;\n"
SER = "use serde::Serialize;\n#[derive(Serialize)]\n" + S
E = '#[error("e")]\npub struct E;\n'

# name: (files, expected verdict)
IDENTITY = {
    "serde_feature_use": (lib("use serde::{Deserialize, Serialize};\n#[derive(Serialize, Deserialize)]\n" + S), TRUSTED),
    "serde_nofeature_noglob": (lib(SER, 'serde = "1"\n'), TRUSTED),
    "serde_nofeature_glob": (lib("use serde::Serialize;\nuse other::*;\n#[derive(Serialize)]\n" + S, 'serde = "1"\nother = "1"\n'), REFUSED),
    "serde_glob_in_block_first": (lib("use serde::Serialize;\npub fn f() {\n    use other::*;\n    #[derive(Serialize)]\n    struct S;\n}\n", SERDE + 'other = "1"\n'), REFUSED),
    # a library of the graph called serde_derive takes the name over (cargo run: `inherent from the other derive`), whatever the manifests say
    "serde_derive_direct": (lib("use serde_derive::Serialize;\n#[derive(Serialize)]\n" + S, 'serde = "1"\nserde_derive = "1"\n'), REFUSED),
    "serde_derive_deserialize": (lib("use serde_derive::Deserialize;\n#[derive(Deserialize)]\n" + S, 'serde = "1"\nserde_derive = "1"\n'), REFUSED),
    "serde_derive_leading_colons": (lib("use ::serde_derive::Serialize;\n#[derive(Serialize)]\n" + S, 'serde = "1"\nserde_derive = "1"\n'), REFUSED),
    "serde_derive_path_written": (lib("#[derive(serde_derive::Serialize)]\n" + S, 'serde = "1"\nserde_derive = "1"\n'), REFUSED),
    "serde_derive_without_serde": (lib("use serde_derive::Serialize;\n#[derive(Serialize)]\n" + S, 'serde_derive = "1"\n'), REFUSED),
    # `../fake` is written beside the repo: a path dependency outside it whose Cargo.toml cannot be read may be a library called `std`, which
    # the prelude check (the first guard, since #30) refuses before the manifest does, so the case would not reach the guard it names
    "serde_path_dep": (lib(SER, 'serde = { path = "../fake", features = ["derive"] }\n', files={"../fake/Cargo.toml": '[package]\nname = "fake"\nversion = "0.1.0"\n'}), REFUSED),
    "serde_crate_attr": (lib('use serde::Serialize;\n#[derive(Serialize)]\n#[serde(crate = "self::s")]\n' + S), REFUSED),
    "serde_crate_attr_cfg_attr": (lib('#[derive(serde::Serialize)]\n#[cfg_attr(feature = "x", serde(crate = "self::s"))]\n' + S), REFUSED),
    "serde_remote": (lib('use serde::Serialize;\n#[derive(Serialize)]\n#[serde(remote = "Other")]\n' + S), TRUSTED),
    "serde_path_written": (lib("#[derive(serde::Serialize, ::serde::Deserialize)]\n" + S), TRUSTED),
    "serde_local_mod_path": (lib("mod serde { pub use other::Serialize; }\n#[derive(serde::Serialize)]\n" + S), REFUSED),
    "serde_use_alias": (lib("use fake::Ser as Serialize;\n#[derive(Serialize)]\n" + S, SERDE + 'fake = "1"\n'), REFUSED),
    "serde_two_uses": (lib("use serde::Serialize;\nuse fake::Serialize;\n#[derive(Serialize)]\n" + S, SERDE + 'fake = "1"\n'), REFUSED),
    "serde_cfg_use": (lib('#[cfg(feature = "x")]\nuse serde::Serialize;\n#[derive(Serialize)]\n' + S), REFUSED),
    "serde_macro_use_extern": (lib("#[macro_use]\nextern crate serde_derive;\n#[derive(Serialize)]\n" + S, 'serde = "1"\nserde_derive = "1"\n'), REFUSED),
    "serde_repo_macro_named": (lib("macro_rules! Serialize { () => {} }\nuse serde::Serialize;\n#[derive(Serialize)]\n" + S), REFUSED),
    "serde_module_macro_call": (lib("use serde::Serialize;\nsome_macro!();\n#[derive(Serialize)]\n" + S), REFUSED),
    "serde_no_use": (lib("#[derive(Serialize)]\n" + S), REFUSED),
    "serde_edition_2015": (lib(SER, edition="2015"), REFUSED),
    "serde_patch": (lib(SER, extra='\n[patch.crates-io]\nserde_derive = { path = "x" }\n'), REFUSED),
    "serde_lock_git": (lib(SER, files={"Cargo.lock": lock(("serde", "1.0.228", CIO), ("serde_derive", "1.0.228", 'source = "git+https://example.invalid/serde"'))}), REFUSED),
    "serde_lock_ok": (lib(SER, files={"Cargo.lock": lock(("serde", "1.0.228", CIO), ("serde_derive", "1.0.228", CIO))}), TRUSTED),
    "serde_in_block": (lib("pub fn f() {\n    use serde::Serialize;\n    #[derive(Serialize)]\n    struct S;\n}\n"), TRUSTED),
    "serde_import_in_parent_only": (lib("use serde::Serialize;\nmod m {\n    #[derive(Serialize)]\n    pub struct S;\n}\n"), REFUSED),
    "serde_super_glob": (lib("use serde::Serialize;\nmod m {\n    use super::*;\n    #[derive(Serialize)]\n    pub struct S;\n}\n"), REFUSED),
    "repo_package_serde": (
        {
            "Cargo.toml": '[workspace]\nmembers = ["a", "serde"]\n',
            "a/Cargo.toml": manifest(SERDE).replace('name = "case"', 'name = "a"'),
            "a/src/lib.rs": SER,
            "serde/Cargo.toml": manifest("").replace('name = "case"', 'name = "serde"'),
            "serde/src/lib.rs": "pub trait Serialize {}\n",
        },
        REFUSED,
    ),
    "clap_plain": (main("use clap::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n"), TRUSTED),
    "clap_local_mod": (main("mod clap { pub trait Parser {} }\nuse ::clap::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n"), REFUSED),
    "clap_renamed": (main("use clap4::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n", 'clap4 = { package = "clap", version = "4", features = ["derive"] }\n'), REFUSED),
    "clap_extern_alias": (main("extern crate fake as clap;\nuse clap::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n", CLAP + 'fake = "1"\n'), REFUSED),
    "thiserror_plain": (lib('use thiserror::Error;\n#[derive(Debug, Error)]\n' + E, TE), TRUSTED),
    "thiserror_local_std": (lib('mod std { pub mod error { pub trait Error {} } }\nuse thiserror::Error;\n#[derive(Debug, Error)]\n' + E, TE), REFUSED),
    "thiserror_glob_then_use": (lib('use thiserror::Error;\nuse other::*;\n#[derive(Debug, Error)]\n' + E, TE + 'other = "1"\n'), REFUSED),
    "beside_unknown_derive": (lib("use serde::Serialize;\nuse other::Thing;\n#[derive(Serialize, Thing)]\n" + S, SERDE + 'other = "1"\n'), REFUSED),
    "clap4_lock_helper_import_elsewhere": (
        main(
            "mod other { #[allow(unused_imports)] use x::command; }\nuse clap::Parser;\n#[derive(Parser)]\n#[command(version)]\nstruct Cli;\nfn main() {}\n",
            CLAP,
        )
        | {"Cargo.lock": lock(("clap", "4.6.7", CIO), ("clap_derive", "4.6.7", CIO))},
        TRUSTED,  # `command` is clap 4's helper only: the lock lists clap_derive 4.6.7, which declares it (tests/test_rs_derive_trust_helpers.py)
    ),
    "clap3_lock_command_head": (
        main(
            "mod other { #[allow(unused_imports)] use x::command; }\nuse clap::Parser;\n#[derive(Parser)]\n#[command(version)]\nstruct Cli;\nfn main() {}\n",
            CLAP,
        )
        | {"Cargo.lock": lock(("clap", "3.2.25", CIO), ("clap_derive", "3.2.25", CIO))},
        REFUSED,
    ),
    "serde_unknown_head": (lib("use serde::Serialize;\n#[derive(Serialize)]\n#[frobnicate]\n" + S), REFUSED),
}


def test_the_adversarial_identity_cases_number_forty_one():
    assert len(IDENTITY) == 41


@pytest.mark.parametrize("name", sorted(IDENTITY))
def test_a_derive_is_trusted_where_the_repo_proves_it_and_refused_where_it_does_not(make_repo, name):
    files, want = IDENTITY[name]
    assert verdict(make_repo, files) == want


def reasons_of(make_repo, files):
    """Why each derive site of the repo was refused (None where it is trusted), in source order."""
    index, _ = read(make_repo, files)
    return [site.refused for ctx in index.ctxs for site in ctx.derive_sites]


# name -> a fragment of the reason the guard that refuses it gives
REASONS = {
    "serde_nofeature_glob": "`serde` here is",
    "serde_glob_in_block_first": "a glob import may bring another Serialize",
    "serde_derive_direct": "Serialize is imported as [['serde_derive', 'Serialize']]",
    "serde_derive_deserialize": "Deserialize is imported as [['serde_derive', 'Deserialize']]",
    "serde_derive_leading_colons": "Serialize is imported as",
    "serde_derive_without_serde": "Serialize is imported as [['serde_derive', 'Serialize']]",
    "serde_path_dep": "manifest (serde): the dependency serde is not a plain registry version",
    "serde_crate_attr": "#[serde(crate = ..)]",
    "serde_crate_attr_cfg_attr": "#[serde(crate = ..)]",
    "serde_local_mod_path": "`serde` here is",
    "serde_use_alias": "Serialize is imported as",
    "serde_two_uses": "Serialize is imported as",
    "serde_cfg_use": "under a #[cfg]",
    "serde_macro_use_extern": "a #[macro_use] extern crate may bring a macro called derive",
    "serde_repo_macro_named": "a macro of the repo is called Serialize",
    "serde_module_macro_call": "`serde` here is",
    "serde_no_use": "no use brings Serialize in",
    "serde_edition_2015": "edition",
    "serde_patch": "manifest (serde): Cargo.toml has a [patch.crates-io] entry",
    "serde_lock_git": "Cargo.lock takes serde_derive from git",
    "serde_import_in_parent_only": "no use brings Serialize in",
    "serde_super_glob": "a glob import may bring another Serialize",
    "repo_package_serde": "a package of the repo is called serde",
    "clap_local_mod": "expansion: `clap` here is",
    "clap_renamed": "Parser is imported as",
    "clap_extern_alias": "`clap` here is",
    "thiserror_local_std": "expansion: `std` here is",
    "thiserror_glob_then_use": "`thiserror` here is",
}
# refused before any identity is asked: another derive or attribute macro on the item, or a bare attribute that is not certainly a helper
NOT_QUEUED = ["beside_unknown_derive", "serde_unknown_head", "clap3_lock_command_head", "serde_derive_path_written"]


@pytest.mark.parametrize("name", sorted(REASONS))
def test_each_refusal_is_the_guards_own(make_repo, name):
    """Every refused case above stops at the check the investigation named for it, not at another that happens to catch it too."""
    files, want = IDENTITY[name]
    assert want == REFUSED
    [why] = reasons_of(make_repo, files)
    assert why is not None and REASONS[name] in why, why


@pytest.mark.parametrize("name", NOT_QUEUED)
def test_a_derive_beside_something_that_may_generate_items_is_never_a_site_to_prove(make_repo, name):
    files, want = IDENTITY[name]
    assert want == REFUSED and reasons_of(make_repo, files) == []


def signature(index, graph):
    """Everything ripple says about the repo that a derive can move: each edge and unresolved call, the externals, the scopes that may
    hold a macro's names, the reasons the impls may be incomplete and the taint each crate carries."""
    scopes = [(s.attribute_macro, s.item_macros, sorted(s.items)) for ctx in index.ctxs for s in index.all_scopes(ctx)]
    return (
        sorted((e.caller, e.kind, e.callee) for e in graph.edges),
        sorted((u.caller, u.line, u.text, u.reason) for u in graph.unresolved),
        graph.external_calls,
        sorted(graph.external_reasons.items()),
        scopes,
        [list(ctx.attr_reasons()) for ctx in index.ctxs],
        [list(ctx.taint) for ctx in index.ctxs],
    )


def as_before(monkeypatch):
    """The ripple of before the rule: no derive is one of the trusted ones."""
    monkeypatch.setattr(rs, "TRUSTED_DERIVES", {})


# Sites the repo does not prove, with calls beside them that a derive's marks would change.
PROBE = "mod probe_mod {\n    pub fn one() {}\n}\nuse probe_mod::*;\nstruct T;\ntrait Tr {\n    fn make();\n}\nimpl Tr for T {\n    fn make() {}\n}\nfn f() {\n    one();\n    T::make();\n    let _ = String::new();\n}\n"


@pytest.mark.parametrize("name", [*sorted(REASONS), *NOT_QUEUED])
def test_a_refused_site_reads_exactly_as_the_same_derive_did_before_the_rule(make_repo, monkeypatch, name):
    files, _ = IDENTITY[name]
    files = {**files, **{p: s + "\n" + PROBE for p, s in files.items() if p.endswith(("lib.rs", "main.rs"))}}
    got = signature(*read(make_repo, files))
    as_before(monkeypatch)
    assert got == signature(*read(make_repo, files))


# What the comparison above could not see: it holds one head at most, so which of two heads comes first (and so which reason `impl_taint`
# answers, `taint[0]`) was never asked. A derive that is not a standard one with an attribute of its own, written after the site and
# before it, puts a second head on each side of the refused site's own.
HEADS = "#[derive(Zed)]\n#[zed]\nstruct Z;\n"


@pytest.mark.parametrize("name", [*sorted(REASONS), *NOT_QUEUED])
@pytest.mark.parametrize("where", ["before", "after"])
def test_a_refused_site_reads_as_before_with_heads_of_another_derive_on_each_side_of_it(make_repo, monkeypatch, name, where):
    """The reasons and their order: the refused site's helpers belong where the site is, not after everything met in source order."""
    files, _ = IDENTITY[name]
    files = {
        **files,
        **{p: (HEADS + s if where == "before" else s + "\n" + HEADS) + "\n" + PROBE for p, s in files.items() if p.endswith(("lib.rs", "main.rs"))},
    }
    got = signature(*read(make_repo, files))
    as_before(monkeypatch)
    assert got == signature(*read(make_repo, files))


def test_the_probe_calls_tell_a_trusted_site_from_a_refused_one(make_repo, monkeypatch):
    """The comparison above is not vacuous: where the derive is trusted, `one()`, `T::make()` and `String::new()` are not the
    unresolved calls they are under a derive that is not a standard one."""
    files = lib("use thiserror::Error;\n#[derive(Debug, Error)]\n" + E + PROBE, TE)  # (serde's and clap's imports refuse beside the probe's glob)
    trusted = signature(*read(make_repo, files))
    as_before(monkeypatch)
    before = signature(*read(make_repo, files))
    edges, unresolved = trusted[0], trusted[1]
    assert sorted(callee for caller, kind, callee in edges if caller == "src/lib:f") == ["src/lib:<T as Tr>::make", "src/lib:probe_mod::one"], edges
    assert unresolved == []
    reasons = {reason for *_, reason in before[1]}
    assert rs.MACRO_MAY_MAKE in reasons and any(r.startswith(rs.IMPLS_INCOMPLETE.split("{")[0]) for r in reasons), reasons
