"""A Cargo.lock decides what the trusted derives are only where it still agrees with the manifests (review finding M1).

Cargo keeps a lock entry only while it satisfies every requirement the manifests write for it, and re-resolves the rest on the next build:
with `clap = "=3.2.25"` and a lock that says 4.6.7 the build uses clap_derive 3.2.25 (whose Parser declares no `#[command]`), and with
`serde = "=1.0.180"` and a lock at 1.0.228 it builds the precompiled serde_derive. So:

  * a locked version counts only if it satisfies the requirement of each dependency the manifests of the lock's workspace write for the
    crate the derive comes through (cargo's own matching: caret with the 0.x rules, `=`, `~`, comparison lists, wildcards, pre-releases;
    one ripple cannot read makes the lock unusable); an unusable lock is no lock for that crate;
  * with no usable lock the helper table stays the version-independent one, and serde's derives are refused when the serde_derive cargo
    resolves is one of the 13 precompiled versions: the newest published version that satisfies the requirements (and serde's own pin of
    its derive, `=<its version>` from 1.0.103 to 1.0.219), refused when it cannot be told.

Every test here fails on the commit before the one that fixes it.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402
from test_rs_derive_trust import CIO, CLAP, REFUSED, S, SERDE, TRUSTED, lib, lock, main, manifest, read, reasons_of, verdict  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

DATA = FIXTURES / "rust-probes" / "derive_trust"


# ---- the requirement reader answers as the semver crate cargo parses with does


def semver_rows():
    lines = (DATA / "semver_cases.tsv").read_text().splitlines()
    versions = next(line for line in lines if line.startswith("# versions\t")).split("\t")[1].split()
    return versions, [line.split("\t") for line in lines if not line.startswith("#")]


def test_the_fixture_holds_the_answers_of_the_semver_crate_for_a_grid_of_requirements_and_versions():
    versions, rows = semver_rows()
    assert len(versions) == 44 and len(rows) >= 450
    assert all(len(letters) == len(versions) for _, letters in rows)
    assert {c for _, letters in rows for c in letters} == {"T", "F", "E"}


def test_every_requirement_reads_as_the_semver_crate_reads_it_and_matches_the_versions_it_does():
    versions, rows = semver_rows()
    wrong = []
    for req, letters in rows:
        if letters == "E" * len(versions):
            if rs.parse_requirement(req) is not None:
                wrong.append((req, "accepted what the crate rejects"))
            continue
        if rs.parse_requirement(req) is None:
            wrong.append((req, "rejected what the crate accepts"))
            continue
        got = "".join("T" if rs.requirement_matches(req, v) else "F" for v in versions)
        if got != letters:
            wrong.append((req, [(v, a, b) for v, a, b in zip(versions, got, letters) if a != b][:3]))
    assert not wrong, wrong[:10]


@pytest.mark.parametrize("version", ["", "1", "1.2", "x", "1.2.3.4", "01.2.3", "1.2.3-", "banana"])
def test_a_version_that_is_not_one_matches_nothing(version):
    assert not rs.requirement_matches("*", version)
    assert not rs.requirement_matches(">=0.0.0", version)


# ---- which published serde_derive versions there are, and what serde pins


def fixture_lines(name):
    return [line.split("\t") for line in (DATA / name).read_text().splitlines()]


def test_the_published_versions_in_the_code_are_those_of_the_index():
    assert list(rs.SERDE_DERIVE_VERSIONS) == [v for v, *_ in fixture_lines("serde_derive.versions.txt")]
    assert list(rs.SERDE_VERSIONS) == [v for v, *_ in fixture_lines("serde.versions.tsv")]
    assert len(rs.SERDE_DERIVE_VERSIONS) == 271 and len(rs.SERDE_VERSIONS) == 316


def test_the_precompiled_versions_are_every_published_one_from_1_0_172_alpha_0_to_1_0_183():
    inside = {v for v in rs.SERDE_DERIVE_VERSIONS if rs.version_key("1.0.172-alpha.0") <= rs.version_key(v) <= rs.version_key("1.0.183")}
    assert inside == rs.PRECOMPILED_SERDE_DERIVE and len(inside) == 13


def test_serde_pins_its_derive_to_its_own_version_from_1_0_103_to_1_0_219_and_no_other():
    """The rule in the code against the requirement every published serde puts on serde_derive (the `=<version>` entry, optional up to
    1.0.185 and a `cfg(any())` target dependency from 1.0.186; from 1.0.220 serde_core is pinned and serde_derive is `^1`)."""
    pinned = {v: any(req == f"={v}" for req in reqs.split(";")) for v, reqs, _ in fixture_lines("serde.versions.tsv")}
    assert {v: rs.serde_derive_pin(v) for v in rs.SERDE_VERSIONS} == pinned
    assert sum(pinned.values()) == 118  # 1.0.103 to 1.0.219: 117 releases and 1.0.172-alpha.0


# ---- a lock agrees with the manifests


def package(locked, **requirements):
    return SimpleNamespace(locked=locked, requirements={k: tuple(v) for k, v in requirements.items()})


def clap_lock(version):
    return {"clap": (version,), "clap_derive": (version,)}


# (requirement, locked version, whether cargo keeps that lock entry)
AGREE = [
    # bare and caret, with the 0.x rules
    ("4", "4.6.7", True), ("4.6", "4.6.7", True), ("4.6.7", "4.6.7", True), ("^4.6.7", "4.6.7", True), ("^4.6", "4.6.7", True), ("4.7", "4.6.7", False),
    ("4.6.8", "4.6.7", False), ("3", "4.6.7", False), ("5", "4.6.7", False), ("0.4", "0.4.9", True), ("0.4", "0.5.0", False), ("0.4.2", "0.4.1", False),
    ("0.0.3", "0.0.3", True), ("0.0.3", "0.0.4", False), ("^0", "0.9.9", True), ("^0", "1.0.0", False), ("0", "0.0.1", True),
    # exact, tilde
    ("=4.6.7", "4.6.7", True), ("=3.2.25", "4.6.7", False), ("=4.6", "4.6.7", True), ("=4", "4.6.7", True), ("=4.6.7", "4.6.8", False),
    ("~4.6", "4.6.7", True), ("~4.6.7", "4.6.7", True), ("~4.6.8", "4.6.7", False), ("~4.5", "4.6.7", False), ("~4", "4.6.7", True), ("~4", "5.0.0", False),
    # comparison lists
    (">=4, <5", "4.6.7", True), (">=4,<5", "4.6.7", True), (">= 4 , < 5", "4.6.7", True), (">=1.0, <1.0.184", "1.0.228", False), (">=1.0, <1.0.184", "1.0.183", True),
    (">4.6.7", "4.6.7", False), ("<=4.6.7", "4.6.7", True), ("<4.6.7", "4.6.7", False), (">=4.6.7, <4.6.7", "4.6.7", False),
    # wildcards
    ("4.*", "4.6.7", True), ("4.6.*", "4.6.7", True), ("4.5.*", "4.6.7", False), ("*", "4.6.7", True), ("3.*", "4.6.7", False), ("4.x", "4.6.7", True),
    # pre-releases: a version with one is matched only by a comparator that names the same major.minor.patch with a pre-release
    ("4", "4.7.0-rc.1", False), ("^4.7.0-rc.1", "4.7.0-rc.1", True), ("=4.7.0-rc.1", "4.7.0-rc.1", True), ("^4.7.0-rc.1", "4.7.0-rc.2", True),
    ("^4.7.0-rc.2", "4.7.0-rc.1", False), (">=4.7.0-rc.1", "4.7.0-rc.2", True), (">=4.6.0", "4.7.0-rc.1", False), ("*", "4.7.0-rc.1", False),
    (">=4.7.0-rc.1, <4.8", "4.7.0", True), ("=4.7.0", "4.7.0-rc.1", False),
    # build metadata is no part of the version
    ("=4.6.7", "4.6.7+deprecated", True),
    # unreadable: the lock cannot be told to agree
    ("banana", "4.6.7", False), ("", "4.6.7", False), ("4.x.y", "4.6.7", False), ("4.6.7.1", "4.6.7", False), ("1.2.3 || 4", "4.6.7", False), (">=", "4.6.7", False),
    ("01", "1.0.0", False), ("4,", "4.6.7", False), ("v4", "4.6.7", False),
]


@pytest.mark.parametrize("requirement, version, agrees", AGREE)
def test_a_locked_version_counts_only_where_it_satisfies_the_manifests_requirement(requirement, version, agrees):
    assert rs.lock_agrees(package(clap_lock(version), clap=[requirement]), "clap_derive") is agrees


def test_a_lock_that_lists_two_versions_agrees_when_each_requirement_has_one_that_satisfies_it():
    both = {"clap": ("3.2.25", "4.6.7"), "clap_derive": ("3.2.25", "4.6.7")}
    assert rs.lock_agrees(package(both, clap=["=3.2.25"]), "clap_derive")
    assert rs.lock_agrees(package(both, clap=["4"]), "clap_derive")
    assert not rs.lock_agrees(package(both, clap=["5"]), "clap_derive")


def test_every_requirement_on_the_crate_must_be_met_not_only_the_first():
    """Cargo unifies the requirements of every manifest of a workspace and of every table of one (`[dependencies]`, `[dev-dependencies]`)."""
    assert rs.lock_agrees(package(clap_lock("4.6.7"), clap=["4", "4.6"]), "clap_derive")
    assert not rs.lock_agrees(package(clap_lock("4.6.7"), clap=["4", "=3.2.25"]), "clap_derive")
    assert not rs.lock_agrees(package(clap_lock("4.6.7"), clap=["=3.2.25", "4"]), "clap_derive")


def test_a_requirement_ripple_could_not_read_from_a_manifest_makes_the_lock_unusable():
    assert not rs.lock_agrees(package(clap_lock("4.6.7"), clap=["4", None]), "clap_derive")


def test_the_requirements_of_the_crates_that_provide_the_derive_all_count():
    """serde_derive comes through `serde` and through `serde_derive` itself: both requirements must agree with the lock."""
    locked = {"serde": ("1.0.228",), "serde_derive": ("1.0.228",)}
    assert rs.lock_agrees(package(locked, serde=["1"], serde_derive=["1"]), "serde_derive")
    assert not rs.lock_agrees(package(locked, serde=["1"], serde_derive=["=1.0.175"]), "serde_derive")
    assert not rs.lock_agrees(package(locked, serde=["=1.0.180"], serde_derive=["1"]), "serde_derive")
    assert rs.lock_agrees(package(locked, clap=["=3.2.25"]), "serde_derive")  # another crate's requirement is no matter here


def test_no_lock_agrees_with_nothing():
    assert not rs.lock_agrees(package(None, clap=["4"]), "clap_derive")
    assert not rs.lock_agrees(package({}, clap=["4"]), "clap_derive")


def test_a_lock_that_lacks_the_dependency_the_manifest_writes_does_not_agree():
    assert not rs.lock_agrees(package({"clap_derive": ("4.6.7",)}, clap=["4"]), "clap_derive")


# ---- the stale lock of the review: the helper table of the version the lock names, only where the lock holds

STALE_SRC = 'use clap::Parser;\n#[derive(Parser)]\n#[command(name = "x")]\nstruct Cli;\nfn main() {}\n'


def clap_repo(requirement, locked, src=STALE_SRC, member=False):
    deps = f'clap = {{ version = "{requirement}", features = ["derive"] }}\n'
    files = main(src, deps)
    if locked is not None:
        files["Cargo.lock"] = lock(("clap", locked, CIO), ("clap_derive", locked, CIO))
    return files


def test_the_reviewers_stale_lock_reads_command_as_the_helper_of_the_version_the_build_uses_so_it_is_refused(make_repo):
    """`clap = "=3.2.25"` and a lock at 4.6.7: cargo re-resolves to clap_derive 3.2.25, whose Parser has no `command`, so `#[command]` is
    the attribute macro `use pm::command` may name. Before: the lock said 4.6.7, which declares it, and the site was trusted."""
    assert verdict(make_repo, clap_repo("=3.2.25", "4.6.7")) == REFUSED


def test_the_same_lock_with_a_manifest_it_satisfies_still_trusts_the_site_and_the_helper(make_repo):
    assert verdict(make_repo, clap_repo("=4.6.7", "4.6.7")) == TRUSTED
    assert verdict(make_repo, clap_repo("4", "4.6.7")) == TRUSTED


def test_without_a_lock_command_is_no_certain_helper_either(make_repo):
    assert verdict(make_repo, clap_repo("4", None)) == REFUSED


@pytest.mark.parametrize("requirement, trusted", [("^4.6", True), ("~4.6.7", True), (">=4, <5", True), ("4.*", True), ("=4.6.7", True), ("~4.5", False), ("<4.6", False), (">4.6.7", False), ("4.7", False), ("banana", False), ("", False), ("4.x.y", False)])
def test_each_form_of_requirement_decides_whether_the_lock_is_the_one_that_builds(make_repo, requirement, trusted):
    assert verdict(make_repo, clap_repo(requirement, "4.6.7")) == (TRUSTED if trusted else REFUSED)


# the workspace the lock belongs to: requirements inherited from the root, and those of another member
CLAP_ROOT = '[workspace]\nmembers = ["app", "other"]\n[workspace.dependencies]\nclap = { version = "%s", features = ["derive"] }\n'


def clap_workspace(inherited, other=None, locked="4.6.7"):
    files = {"Cargo.toml": CLAP_ROOT % inherited}
    files["app/Cargo.toml"] = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nclap.workspace = true\n'
    files["app/src/main.rs"] = STALE_SRC
    files["other/Cargo.toml"] = '[package]\nname = "other"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\n' + (f'clap = "{other}"\n' if other else "")
    files["other/src/lib.rs"] = ""
    files["Cargo.lock"] = lock(("clap", locked, CIO), ("clap_derive", locked, CIO))
    return files


@pytest.mark.parametrize("inherited, trusted", [("4", True), ("=4.6.7", True), ("=3.2.25", False), ("3", False), ("banana", False)])
def test_a_requirement_inherited_from_the_workspace_is_the_roots(make_repo, inherited, trusted):
    assert verdict(make_repo, clap_workspace(inherited)) == (TRUSTED if trusted else REFUSED)


@pytest.mark.parametrize("other, trusted", [("4", True), ("=3.2.25", False), ("banana", False)])
def test_another_member_of_the_workspace_that_the_lock_does_not_satisfy_makes_it_stale_for_all(make_repo, other, trusted):
    """One lock and one resolution serve the whole workspace: cargo re-resolves clap for every member."""
    assert verdict(make_repo, clap_workspace("4", other)) == (TRUSTED if trusted else REFUSED)


def test_the_requirements_are_read_from_every_table_a_dependency_may_be_written_in(make_repo):
    files = clap_repo("4", "4.6.7")
    files["Cargo.toml"] += '\n[dev-dependencies]\nclap = "=3.2.25"\n'
    assert verdict(make_repo, files) == REFUSED


# ---- serde with no usable lock: the serde_derive cargo resolves is the newest version that satisfies the requirements

SER_SRC = "use serde::Serialize;\n#[derive(Serialize)]\n" + S


def serde_repo(serde, serde_derive=None, locked=None, src=SER_SRC):
    deps = f'serde = {{ version = "{serde}", features = ["derive"] }}\n' + (f'serde_derive = "{serde_derive}"\n' if serde_derive else "")
    files = lib(src, deps)
    if locked:
        files["Cargo.lock"] = lock(("serde", locked, CIO), ("serde_derive", locked, CIO))
    return files


@pytest.mark.parametrize("serde, serde_derive, trusted", [
    # serde alone: its derive is pinned to its version from 1.0.103 to 1.0.219, so the newest serde that satisfies decides
    ("<1.0.184", None, False), ("<=1.0.183", None, False), ("=1.0.180", None, False), ("=1.0.172", None, False), ("=1.0.172-alpha.0", None, False),
    ("=1.0.183", None, False), (">=1.0.150, <1.0.173", None, False), ("~1.0.175", None, True), ("1.0.175", None, True), ("<1.0.172", None, True),
    ("=1.0.171", None, True), ("=1.0.184", None, True), ("<1.0.172-alpha.0", None, True), ("<=1.0.172-alpha.0", None, False),
    # a requirement that resolves past the 13 is trusted
    ("1", None, True), ("1.0", None, True), ("^1.0.100", None, True), ("1.0.184", None, True), (">=1.0.173", None, True), ("*", None, True), ("1.*", None, True),
    (">=1.0.180", None, True), ("^1.0.172", None, True),
    # serde_derive written too: its own requirement, and serde's pin
    ("1", "=1.0.175", False), ("1", "=1.0.172-alpha.0", False), ("1", "<1.0.184", False), ("1", "=1.0.171", True), ("1", "1", True), ("1", "1.0.184", True),
    ("=1.0.180", "1", False), ("=1.0.200", "1", True), ("=1.0.200", "=1.0.175", False), ("=1.0.184", "=1.0.184", True),
    # serde 1.0.220 and later do not pin it, so the version of serde says nothing of its derive
    ("=1.0.228", None, True), ("=1.0.228", "=1.0.175", False),
    # nothing published satisfies, or a requirement ripple cannot read: doubt, refuse
    ("=1.0.230", None, False), ("^2", None, False), ("banana", None, False), ("", None, False), ("1.0.x.y", None, False), ("1", "banana", False), ("1", "^2", False),
])
def test_without_a_lock_a_serde_derive_is_trusted_only_where_the_newest_version_that_satisfies_is_not_a_precompiled_one(make_repo, serde, serde_derive, trusted):
    assert verdict(make_repo, serde_repo(serde, serde_derive)) == (TRUSTED if trusted else REFUSED)


def test_the_refusal_says_which_requirements_lead_to_a_precompiled_derive(make_repo):
    [why] = reasons_of(make_repo, serde_repo("<1.0.184"))
    assert "precompiled" in why and "1.0.183" in why, why
    [why] = reasons_of(make_repo, serde_repo("banana"))
    assert "precompiled" in why and "cannot" in why, why


def test_a_serde_pin_of_the_derive_feature_does_not_hide_the_newest_serde_derive_a_requirement_of_its_own_allows(make_repo):
    """`serde = "=1.0.150"` pins serde_derive 1.0.150 only through the `derive` feature; without it the macro crate that builds is the newest
    that `<1.0.184` allows, 1.0.183, a precompiled one. Only serde's own import is trusted now, which needs the feature (the manifest may be
    for another target, so ripple does not read the feature as proof), so both versions are read: refused where either is precompiled."""
    files = lib(SER_SRC, 'serde = "=1.0.150"\nserde_derive = "<1.0.184"\n')
    assert verdict(make_repo, files) == REFUSED
    assert "1.0.183" in reasons_of(make_repo, files)[0]
    assert verdict(make_repo, lib(SER_SRC, 'serde = "=1.0.150"\nserde_derive = "<1.0.172"\n')) == TRUSTED


def test_a_requirement_for_serde_derive_beside_serde_follows_the_same_rule(make_repo):
    assert verdict(make_repo, serde_repo("1", "=1.0.175")) == REFUSED
    assert verdict(make_repo, serde_repo("1", "1")) == TRUSTED


@pytest.mark.parametrize("serde, serde_derive, version, trusted", [
    # a lock that agrees with the manifests says what builds, as before
    ("1", None, "1.0.175", False), ("1", None, "1.0.183", False), ("1", None, "1.0.228", True), ("<1.0.184", None, "1.0.183", False), ("=1.0.172-alpha.0", None, "1.0.172-alpha.0", False),
    # a lock the manifests no longer satisfy is no lock: the requirements decide
    ("=1.0.180", None, "1.0.228", False), ("<1.0.184", None, "1.0.228", False), ("=1.0.228", None, "1.0.175", True), ("1", "=1.0.175", "1.0.228", False),
    ("1.0.200", None, "1.0.175", True), ("=1.0.200", None, "1.0.175", True),
    # `1` does not match a pre-release, so a lock at one is stale and cargo builds the newest: not a precompiled version
    ("1", "1", "1.0.172-alpha.0", True),
])
def test_a_lock_counts_for_the_precompiled_check_only_where_it_agrees_with_the_manifest(make_repo, serde, serde_derive, version, trusted):
    assert verdict(make_repo, serde_repo(serde, serde_derive, locked=version)) == (TRUSTED if trusted else REFUSED)


def test_the_reviewers_second_example_a_lock_at_1_0_228_does_not_stand_in_for_a_manifest_that_pins_1_0_180(make_repo):
    assert verdict(make_repo, serde_repo("=1.0.180", locked="1.0.228")) == REFUSED
