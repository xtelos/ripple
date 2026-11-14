"""A derive of serde, clap or thiserror is the published crate's only where the crate looks up as the dependency from crates.io, and Rule P
(rs_modules.PATCH_UNPROVEN, README "Redirects") says a dependency that any readable `[patch]` or `[replace]` entry, or a config's `paths`,
names may be another package: it is unresolved, never external. So the derive must be refused for the NAME (`serde` here is not the crate:
Index.derive_path, the same requirement as Index.trust_identity's `("ext", "crate:<name>")`), before the manifests are asked.

The manifest guard (rs_modules._trust refuses a package a `[patch]` of any crate reaches) catches these repos as well, so the reason is
what pins the lookup: a derive trusted through a dependency Rule P marked unproven would be refused by the manifest alone, and the day that
guard is relaxed (a patch of another crate is no reason to refuse serde) this is the check that still holds.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from test_rs_derive_trust import CLAP, REFUSED, SERDE, TE, manifest, read, verdict  # noqa: E402

from ripple import rs_modules  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

S = "pub struct S;\n"
SOURCES = {
    "serde": ("src/lib.rs", "use serde::Serialize;\n#[derive(Serialize)]\n" + S, SERDE),
    "clap": ("src/main.rs", "use clap::Parser;\n#[derive(Parser)]\nstruct Cli;\nfn main() {}\n", CLAP),
    "thiserror": ("src/lib.rs", 'use thiserror::Error;\n#[derive(Debug, Error)]\n#[error("e")]\npub struct E;\n', TE),
}
VENDORED = '[package]\nname = "{crate}"\nversion = "1.0.0"\nedition = "2021"\n'

# name -> (text added to the manifest, the other files, given the crate)
REDIRECTS = {
    "a git patch": (lambda c: f'\n[patch.crates-io]\n{c} = {{ git = "https://example.com/{c}" }}\n', lambda c: {}),
    "a path patch of a package of the repo": (
        lambda c: f'\n[patch.crates-io]\n{c} = {{ path = "vendor/{c}" }}\n',
        lambda c: {f"vendor/{c}/Cargo.toml": VENDORED.format(crate=c), f"vendor/{c}/src/lib.rs": ""},
    ),
    "a [replace] entry": (lambda c: f'\n[replace]\n"{c}:1.0.0" = {{ git = "https://example.com/{c}" }}\n', lambda c: {}),
    "a `paths` override of a cargo config": (
        lambda c: "",
        lambda c: {".cargo/config.toml": f'paths = ["vendor/{c}"]\n', f"vendor/{c}/Cargo.toml": VENDORED.format(crate=c), f"vendor/{c}/src/lib.rs": ""},
    ),
    # any `paths` entry refuses every dependency (rs_modules._config_paths): cargo also loads the path dependencies of each package below it
    "a `paths` entry that holds no package of the crate's name": (
        lambda c: "",
        lambda c: {".cargo/config.toml": 'paths = ["sub"]\n', "sub/Cargo.toml": VENDORED.format(crate="other"), "sub/src/lib.rs": ""},
    ),
    "a `paths` entry whose package reaches the crate by a path dependency": (
        lambda c: "",
        lambda c: {
            ".cargo/config.toml": 'paths = ["sub"]\n',
            "sub/Cargo.toml": VENDORED.format(crate="sub") + f'\n[dependencies]\n{c} = {{ path = "../third/{c}" }}\n',
            "sub/src/lib.rs": "",
            f"third/{c}/Cargo.toml": VENDORED.format(crate=c),
            f"third/{c}/src/lib.rs": "",
        },
    ),
    "a `paths` entry outside the tree": (lambda c: "", lambda c: {".cargo/config.toml": 'paths = ["../outside"]\n'}),
}


def files_of(crate, how):
    path, src, deps = SOURCES[crate]
    extra, others = REDIRECTS[how]
    return {"Cargo.toml": manifest(deps, extra=extra(crate)), path: src, **others(crate)}


@pytest.mark.parametrize("how", sorted(REDIRECTS))
@pytest.mark.parametrize("crate", sorted(SOURCES))
def test_a_dependency_rule_p_marks_unproven_is_not_the_crate_a_derive_is_trusted_through(make_repo, crate, how):
    files = files_of(crate, how)
    index, _ = read(make_repo, files)
    [package] = [c.crate.package for c in index.ctxs if c.derive_sites][:1]
    assert package.deps[crate] == rs_modules.PATCH_UNPROVEN  # Rule P holds for the dependency itself
    [why] = [site.refused for ctx in index.ctxs for site in ctx.derive_sites]
    # a redirect ripple cannot place (a git source, a `paths` entry) also makes `std` and the prelude's `derive` unknown, and that guard names
    # the refusal first; either is a guard of the name, which comes before the manifests
    assert why is not None and why.startswith("name: "), why
    assert verdict(make_repo, files) == REFUSED


@pytest.mark.parametrize("crate", sorted(SOURCES))
def test_a_path_patch_of_a_package_of_the_repo_is_refused_by_the_lookup_of_the_crate(make_repo, crate):
    """The one redirect whose package ripple places (so `std` is still the library's): only the lookup of the crate refuses it before the
    manifests do, which is what makes it the pin of the `("ext", "crate:<name>")` requirement (Index.derive_path, Index.derive_imported)."""
    index, _ = read(make_repo, files_of(crate, "a path patch of a package of the repo"))
    [why] = [site.refused for ctx in index.ctxs for site in ctx.derive_sites]
    assert why is not None and why.startswith(f"name: `{crate}` here is"), why


@pytest.mark.parametrize("crate", sorted(SOURCES))
def test_the_same_derive_with_no_redirect_is_trusted(make_repo, crate):
    """The control: nothing above is refused because of the shape of the repo."""
    path, src, deps = SOURCES[crate]
    index, _ = read(make_repo, {"Cargo.toml": manifest(deps), path: src})
    assert [site.refused for ctx in index.ctxs for site in ctx.derive_sites] == [None]


@pytest.mark.parametrize("crate", sorted(SOURCES))
def test_the_walk_sees_the_redirect_the_tests_above_write(make_repo, crate):
    """Guards the fixtures: the vendored package is one the walk reads, so a path patch is a package of the repo."""
    repo = make_repo(files_of(crate, "a path patch of a package of the repo"))
    found, _ = walk_languages(repo, [RUST])
    assert f"vendor/{crate}/src/lib.rs" in {p.relative_to(repo).as_posix() for p in found[RUST.name]}
