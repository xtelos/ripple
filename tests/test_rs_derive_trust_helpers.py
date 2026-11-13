"""The helper attributes beside a trusted derive: a bare name is the derive's helper (so it is no attribute macro, whatever `use` gives the
name) only if that derive declares it in every version of its macro crate that the build can use.

What each derive declares was read from every published version of serde_derive, clap_derive and thiserror-impl
(bench/fixtures/rust-probes/derive_trust/declared_helpers.tsv). Some names exist in some versions only (clap 4's `command`, `arg`,
`group`, `value`, thiserror 1.0.2's `from` and `backtrace`), so:

  * a Cargo.lock that lists the macro crate says which versions are built: the name must be declared in every one the lock lists;
  * with no lock, only the names every version declares: `serde`, `clap`, `error`, `source`;
  * a bare name that is not certain is read as it always was beside a derive that is not a standard one (an attribute macro unless
    nothing of the crate can bring one by that name), and a derive written beside one is never queued for trust.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402
from test_rs_derive_trust import CIO, CLAP, REFUSED, TRUSTED, as_before, lock, main, read, reasons_of, signature, verdict  # noqa: E402

from ripple import rs_indexer as rs  # noqa: E402

DATA = FIXTURES / "rust-probes" / "derive_trust" / "declared_helpers.tsv"
MACRO_CRATE = {"serde_derive": ("Serialize", "Deserialize"), "clap_derive": ("Parser", "Args", "Subcommand", "ValueEnum"), "thiserror_impl": ("Error",)}


def declared():
    rows = [line.split("\t") for line in DATA.read_text().splitlines()]
    return [(crate, version, derive, None if helpers == "-" else frozenset(h for h in helpers.split(",") if h)) for crate, version, derive, helpers in rows]


def test_the_table_reads_the_declaration_of_every_published_version_of_every_trusted_derive():
    rows = declared()
    assert len(rows) == 1005
    assert {(c, d) for c, _, d, _ in rows} == {(c, d) for c, names in MACRO_CRATE.items() for d in names}
    assert {c: len({v for cc, v, _, _ in rows if cc == c}) for c in MACRO_CRATE} == {"serde_derive": 271, "clap_derive": 93, "thiserror_impl": 91}


@pytest.mark.parametrize("crate", sorted(MACRO_CRATE))
def test_helpers_at_gives_what_each_published_version_declares(crate):
    wrong = [(v, d, got, want) for c, v, d, want in declared() if c == crate and (got := rs.helpers_at(d, v)) != want]
    assert wrong == [], wrong[:5]


def test_a_version_the_table_does_not_know_has_no_certain_helper():
    assert rs.helpers_at("Parser", "not a version") is None and rs.helpers_at("Parser", "") is None
    assert rs.helpers_at("Parser", "4.6.7+build.5") == rs.helpers_at("Parser", "4.6.7")
    assert rs.helpers_at("Serialize", "0.1.0") is None  # older than any published serde_derive


def test_the_helpers_every_version_declares_are_the_no_lock_set():
    """serde from 0.8.18: the earlier ones (0.8.4 to 0.8.17) are 2016 nightly-only derives that no repo with a manifest builds today."""
    for derive, base in rs.DERIVE_HELPERS.items():
        crate = next(c for c, names in MACRO_CRATE.items() if derive in names)
        every = [h for c, _, d, h in declared() if c == crate and d == derive and h is not None and (crate != "serde_derive" or h)]  # (serde_derive 0.8.4 to 0.8.17 declare none)
        assert base == frozenset.intersection(*every), (derive, base)
    assert rs.DERIVE_HELPERS == {
        "Serialize": {"serde"}, "Deserialize": {"serde"}, "Parser": {"clap"}, "Args": {"clap"}, "Subcommand": {"clap"}, "ValueEnum": {"clap"}, "Error": {"error", "source"},
    }


def certain(derives, **locked):
    """The certain helpers of these derives under a lock that lists these versions of each macro crate (none: no lock)."""
    tderives = [(d, d) for d in derives]
    return rs.derive_helpers(tderives, {k: tuple(v) for k, v in locked.items()} if locked else None)


@pytest.mark.parametrize(
    "derives, locked, want",
    [
        pytest.param(["Parser"], {}, {"clap"}, id="no lock: the version-independent set"),
        pytest.param(["Parser"], {"clap_derive": ["4.6.7"]}, {"clap", "structopt", "command", "arg", "group"}, id="clap_derive 4.6.7"),
        pytest.param(["Parser"], {"clap_derive": ["4.0.6"]}, {"clap", "structopt", "command", "arg"}, id="4.0.6 has no group on Parser"),
        pytest.param(["Parser"], {"clap_derive": ["4.0.7"]}, {"clap", "structopt", "command", "arg", "group"}, id="4.0.7 has"),
        pytest.param(["Parser"], {"clap_derive": ["4.0.0-rc.1"]}, {"clap", "structopt", "command", "arg"}, id="4.0.0-rc.1"),
        pytest.param(["Parser"], {"clap_derive": ["3.2.25"]}, {"clap", "structopt"}, id="3.2.25: no command"),
        pytest.param(["Parser"], {"clap_derive": ["3.0.0-beta.5"]}, {"clap"}, id="3.0.0-beta.5"),
        pytest.param(["Parser"], {"clap_derive": ["3.0.0-beta.4"]}, set(), id="3.0.0-beta.4 has no Parser"),
        pytest.param(["Parser"], {"clap_derive": ["3.2.25", "4.6.7"]}, {"clap", "structopt"}, id="two versions: what both declare"),
        pytest.param(["Args"], {"clap_derive": ["4.6.7"]}, {"clap", "command", "arg", "group"}, id="Args 4.6.7"),
        pytest.param(["Subcommand"], {"clap_derive": ["4.1.0"]}, {"clap", "command", "arg", "group"}, id="Subcommand 4.1.0"),
        pytest.param(["ValueEnum"], {"clap_derive": ["4.6.7"]}, {"clap", "value"}, id="ValueEnum 4.6.7"),
        pytest.param(["ValueEnum"], {"clap_derive": ["3.2.25"]}, {"clap"}, id="ValueEnum 3.2.25"),
        pytest.param(["Error"], {"thiserror_impl": ["1.0.0"]}, {"error", "source"}, id="thiserror-impl 1.0.0"),
        pytest.param(["Error"], {"thiserror_impl": ["1.0.69"]}, {"error", "source", "from", "backtrace"}, id="thiserror-impl 1.0.69"),
        pytest.param(["Error"], {"thiserror_impl": ["2.0.21", "1.0.1"]}, {"error", "source"}, id="a 1.0.1 beside a 2.0.21"),
        pytest.param(["Serialize"], {"serde_derive": ["1.0.228"]}, {"serde"}, id="serde_derive 1.0.228"),
        pytest.param(["Serialize"], {"serde_derive": ["0.8.17"]}, set(), id="serde_derive 0.8.17 declares none"),
        pytest.param(["Serialize", "Deserialize"], {"serde_derive": ["0.8.18", "1.0.228"]}, {"serde"}, id="serde_derive 0.8.18"),
        pytest.param(["Parser", "Error"], {"clap_derive": ["4.6.7"], "thiserror_impl": ["1.0.69"]}, {"clap", "structopt", "command", "arg", "group", "error", "source", "from", "backtrace"}, id="the union over the derives of the item"),
        pytest.param(["Parser"], {"thiserror_impl": ["1.0.69"]}, {"clap"}, id="a lock that lists no clap_derive: the no-lock set (the manifest check refuses it anyway)"),
    ],
)
def test_the_certain_helpers_are_what_every_locked_version_declares(derives, locked, want):
    assert certain(derives, **locked) == want


def clap_lock(version, derive="clap_derive"):
    return {"Cargo.lock": lock(("clap", version, CIO), (derive, version, CIO))}


def parser(attr, rest=""):
    return main(f"{rest}use clap::Parser;\n#[derive(Parser)]\n{attr}\nstruct Cli;\nfn main() {{}}\n")


@pytest.mark.parametrize(
    "files, want",
    [
        pytest.param(parser("#[command(version)]") | clap_lock("4.6.7"), TRUSTED, id="a lock that proves command"),
        pytest.param(parser("#[command(version)]") | clap_lock("4.0.0"), TRUSTED, id="4.0.0 does too"),
        pytest.param(parser("#[command(version)]") | clap_lock("3.2.25"), REFUSED, id="a lock that does not"),
        pytest.param(parser("#[command(version)]"), REFUSED, id="no lock: command is clap 4's only"),
        pytest.param(parser("#[clap(version)]"), TRUSTED, id="no lock: clap, which every version declares"),
        pytest.param(parser("#[clap(version)]") | clap_lock("3.2.25"), TRUSTED, id="clap with a clap 3 lock"),
        pytest.param(parser("#[group(multiple = true)]") | clap_lock("4.0.6"), REFUSED, id="group on Parser is 4.0.7's"),
        pytest.param(parser("#[group(multiple = true)]") | clap_lock("4.0.7"), TRUSTED, id="and 4.0.7 has it"),
        pytest.param(parser("#[command(version)]\n#[clap(author)]") | clap_lock("4.6.7"), TRUSTED, id="two helpers, both proven"),
        pytest.param(parser("#[command(version)]\n#[frobnicate]") | clap_lock("4.6.7"), REFUSED, id="one proven, one not"),
        pytest.param(parser("#[structopt(name = \"x\")]") | clap_lock("4.6.7"), TRUSTED, id="structopt is declared by Parser"),
        pytest.param(parser("#[structopt(name = \"x\")]") | clap_lock("3.0.0-beta.5"), REFUSED, id="and not by 3.0.0-beta.5"),
    ],
)
def test_a_bare_attribute_beside_a_clap_derive_is_its_helper_only_where_the_lock_proves_it(make_repo, files, want):
    assert verdict(make_repo, files) == want


def test_a_lock_listing_a_clap_derive_that_declares_it_beats_an_import_of_an_attribute_macro_by_the_name(make_repo):
    """MEASURED (_derive_trust/p3a, p3b): a declared helper beats an attribute macro of the name. fd's `#[command]` is not inert under the
    old rule because some `use` of the crate gives the name `command`."""
    files = parser("#[command(version)]", "mod other {\n    #[allow(unused_imports)]\n    use x::command;\n}\n") | clap_lock("4.6.7")
    assert verdict(make_repo, files) == TRUSTED
    files = parser("#[command(version)]", "mod other {\n    #[allow(unused_imports)]\n    use x::command;\n}\n") | clap_lock("3.2.25")
    assert verdict(make_repo, files) == REFUSED


def test_a_refused_site_whose_helper_the_lock_proved_is_read_as_it_always_was(make_repo, monkeypatch):
    """The helper was taken on the derive's word; a refusal (here a module called clap) puts the name back as an attribute macro of the
    crate wherever a `use` gives it."""
    src = "mod clap {\n    pub trait Tr {}\n}\nuse x::command;\nuse ::clap::Parser;\n#[derive(Parser)]\n#[command(version)]\nstruct Cli;\nfn main() {}\n"
    files = main(src, CLAP + 'x = "1"\n') | clap_lock("4.6.7")
    got = signature(*read(make_repo, files))
    [why] = reasons_of(make_repo, files)
    assert why is not None and "`clap` here is" in why
    as_before(monkeypatch)
    assert got == signature(*read(make_repo, files))


def test_a_helper_the_lock_cannot_prove_never_queues_the_site(make_repo):
    files = parser("#[command(version)]") | clap_lock("3.2.25")
    assert reasons_of(make_repo, files) == []
