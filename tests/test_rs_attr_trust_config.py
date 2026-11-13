"""Cargo config files, and the trusted attribute macros.

Cargo reads `.cargo/config.toml` (and the older `.cargo/config`) from the directory it is run in upward, which may be anywhere
in a repo, so every one in the repo is read. A site is trusted only when every key of every one is a setting that cannot change
which code a crate name or `rustc` means (rs_modules.CONFIG_KEYS), and any rustflags are codegen options, lint levels or cfgs.
Anything else, a key that is not listed included, refuses every package of the repo.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple import rs_modules  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PKG = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
TOKIO = '[dependencies]\ntokio = "1"\n'
MAIN = "#[tokio::main]\nasync fn main() {\n    helper();\n}\n\nfn helper() {}\n"
TRUSTED, UNTRUSTED = "edge", [rs.ATTR_MACRO]


def verdict(make_repo, files):
    g = build_graph(make_repo(files), RUST)
    edges = [e for e in g.edges if e.kind == "call" and e.callee.endswith("helper")]
    reasons = sorted({u.reason for u in g.unresolved if u.text.endswith("helper")})
    assert bool(edges) != bool(reasons), (edges, reasons)
    return "edge" if edges else reasons


def with_config(text, path=".cargo/config.toml"):
    return {"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, path: text}


# what a config may set and leave the crates as they are
HARMLESS = {
    "target_rustflags_only": '[target.x86_64-pc-windows-msvc]\nrustflags = ["-C", "target-feature=+crt-static"]\n',
    "target_cfg_expression_with_link_args": "[target.'cfg(all(target_os = \"windows\", target_env = \"msvc\"))']\nrustflags = [\n  \"-C\", \"link-arg=/DELAYLOAD:shell32.dll\",\n  \"-C\", \"link-arg=/IGNORE:4199\",\n]\n",
    "target_musl": '[target.x86_64-unknown-linux-musl]\nrustflags = ["-C", "target-feature=+crt-static", "-C", "link-self-contained=yes"]\n',
    "resolver_incompatible_rust_versions": '[resolver]\nincompatible-rust-versions = "fallback"\n',
    "build_jobs_and_target_dir": '[build]\njobs = 2\ntarget-dir = "out"\n',
    "build_rustflags_string": '[build]\nrustflags = "-C target-cpu=native -D warnings"\n',
    "build_rustflags_cfg_and_lints": '[build]\nrustflags = ["--cfg", "tokio_unstable", "-Dwarnings", "-A", "dead_code", "-Ccodegen-units=4", "--cfg=foo"]\n',
    "linker_and_runner": '[target.x86_64-unknown-linux-gnu]\nlinker = "clang"\nrunner = "qemu"\n',
    "term_and_net": '[term]\ncolor = "always"\n[net]\nretry = 5\ngit-fetch-with-cli = true\n',
    "env_that_is_no_compiler": '[env]\nRUST_LOG = "debug"\nFOO = { value = "bar", force = true }\n',
    "empty_file": "",
    "only_comments": "# nothing here\n",
}
# what it may not: each can make a crate, or rustc, something else
HARMFUL = {
    "paths": 'paths = ["../tokio-macros"]\n',
    "source_replacement": '[source.crates-io]\nreplace-with = "v"\n[source.v]\ndirectory = "vendor"\n',
    "patch": '[patch.crates-io]\ntokio-macros = { path = "m" }\n',
    "patch_of_something_else": '[patch.crates-io]\nsomething = { path = "m" }\n',
    "include": 'include = ["more.toml"]\n',
    "registries": '[registries.corp]\nindex = "https://example.invalid/index"\n',
    "registry_default": '[registry]\ndefault = "corp"\n',
    "registry_crates_io_index": '[registries.crates-io]\nindex = "https://example.invalid/index"\n',
    "resolver_lockfile_path": '[resolver]\nlockfile-path = "x/Cargo.lock"\n',
    "resolver_lockfile_path_dotted": 'resolver.lockfile-path = "x/Cargo.lock"\n',
    "build_rustc": '[build]\nrustc = "my-rustc"\n',
    "build_rustc_wrapper": '[build]\nrustc-wrapper = "wrap"\n',
    "build_rustc_workspace_wrapper": '[build]\nrustc-workspace-wrapper = "wrap"\n',
    "build_rustdoc": '[build]\nrustdoc = "my-rustdoc"\n',
    "build_an_unknown_key": "[build]\nfrobnicate = 1\n",
    "env_rustc": '[env]\nRUSTC = "my-rustc"\n',
    "env_rustc_wrapper": '[env]\nRUSTC_WRAPPER = "wrap"\n',
    "env_rustflags": '[env]\nRUSTFLAGS = "--extern tokio=/x"\n',
    "env_rustdoc": '[env]\nRUSTDOC = "x"\n',
    "env_rustup": '[env]\nRUSTUP_TOOLCHAIN = "x"\n',
    "env_cargo": '[env]\nCARGO_BUILD_RUSTC = "x"\n',
    "env_dyld": '[env]\nDYLD_INSERT_LIBRARIES = "/evil.dylib"\n',
    "env_not_a_table": "env = 1\n",
    "build_not_a_table": "build = 1\n",
    "rustflags_attached_codegen_not_listed": '[build]\nrustflags = ["-Cextra-filename=-x"]\n',
    "env_path": '[env]\nPATH = "/evil"\n',
    "env_ld_preload": '[env]\nLD_PRELOAD = "/evil.so"\n',
    "alias": '[alias]\nt = "test --config patch"\n',
    "profile": "[profile.dev]\nopt-level = 1\n",
    "http_proxy": '[http]\nproxy = "http://evil"\n',
    "an_unknown_top_level_key": "frobnicate = 1\n",
    "an_unknown_table": "[frobnicate]\nx = 1\n",
    "rustflags_extern": '[build]\nrustflags = ["--extern", "tokio=/x/libtokio.rlib"]\n',
    "rustflags_extern_attached": '[build]\nrustflags = ["--extern=tokio=/x/libtokio.rlib"]\n',
    "rustflags_L": '[build]\nrustflags = ["-L", "dependency=/x"]\n',
    "rustflags_L_attached": '[build]\nrustflags = ["-L/x"]\n',
    "rustflags_sysroot": '[build]\nrustflags = ["--sysroot", "/x"]\n',
    "rustflags_z": '[build]\nrustflags = ["-Zunstable-options"]\n',
    "rustflags_crate_attr": '[build]\nrustflags = ["--crate-attr=no_std"]\n',
    "rustflags_string_with_extern": '[build]\nrustflags = "-C target-cpu=native --extern tokio=/x"\n',
    "rustflags_codegen_that_is_not_listed": '[build]\nrustflags = ["-C", "extra-filename=-x"]\n',
    "rustflags_ends_in_a_flag": '[build]\nrustflags = ["-C"]\n',
    "rustflags_not_a_list_of_strings": "[build]\nrustflags = [1]\n",
    "target_rustflags_extern": '[target.x86_64-unknown-linux-gnu]\nrustflags = ["--extern", "tokio=/x"]\n',
    "target_cfg_rustflags_extern": "[target.'cfg(unix)']\nrustflags = [\"--extern=tokio=/x\"]\n",
    "target_build_script_override": '[target.x86_64-unknown-linux-gnu.openssl]\nrustc-link-search = ["/x"]\n',
    "target_that_is_not_a_table": "[target]\nlinker = 1\n",
    "unreadable": "this is [not toml\n",
}


@pytest.mark.parametrize("name", HARMLESS)
def test_a_config_that_sets_only_what_cannot_change_a_crate_leaves_the_site_trusted(make_repo, name):
    assert verdict(make_repo, with_config(HARMLESS[name])) == TRUSTED


@pytest.mark.parametrize("name", HARMFUL)
def test_a_config_that_sets_anything_else_refuses_the_site(make_repo, name):
    assert verdict(make_repo, with_config(HARMFUL[name])) == UNTRUSTED


@pytest.mark.parametrize("name", ["paths", "build_rustc_wrapper", "rustflags_extern"])
def test_the_older_config_file_name_is_read_the_same_way(make_repo, name):
    assert verdict(make_repo, with_config(HARMFUL[name], ".cargo/config")) == UNTRUSTED
    assert verdict(make_repo, with_config(HARMLESS["target_rustflags_only"], ".cargo/config")) == TRUSTED


# where the file is: cargo reads the ones above the directory it is run in, which may be any directory of the repo
PLACES = {
    "beside_the_package": ".cargo/config.toml",
    "in_a_directory_of_the_package": "src/.cargo/config.toml",
    "in_a_directory_below_the_package": "src/deep/er/.cargo/config.toml",
    "in_a_directory_beside_the_package": "docs/.cargo/config.toml",
    "in_a_directory_with_no_source": "tools/scripts/.cargo/config",
    "in_a_hidden_directory": ".devcontainer/.cargo/config.toml",
}


@pytest.mark.parametrize("name", PLACES)
def test_a_harmful_config_anywhere_in_the_repo_refuses_the_package(make_repo, name):
    files = {"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, PLACES[name]: 'paths = ["x"]\n'}
    assert verdict(make_repo, files) == UNTRUSTED


@pytest.mark.parametrize("name", PLACES)
def test_a_harmless_config_anywhere_in_the_repo_leaves_it_trusted(make_repo, name):
    files = {"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, PLACES[name]: HARMLESS["target_rustflags_only"]}
    assert verdict(make_repo, files) == TRUSTED


def test_a_config_above_one_package_refuses_its_sibling_too(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
        "a/Cargo.toml": PKG.replace('"app"', '"a"') + TOKIO,
        "a/src/main.rs": MAIN,
        "b/Cargo.toml": PKG.replace('"app"', '"b"') + TOKIO,
        "b/src/main.rs": MAIN,
        "a/.cargo/config.toml": 'paths = ["x"]\n',
    }
    g = build_graph(make_repo(files), RUST)
    assert not [e for e in g.edges if e.callee.endswith("helper")]
    assert {u.caller for u in g.unresolved if u.text == "helper"} == {"a/src/main:main", "b/src/main:main"}


def test_a_config_in_a_directory_the_walk_does_not_enter_is_not_read(make_repo):
    """`.git` and `node_modules`, and a `target` directory beside a Cargo.toml (where the source walk reads no file either: it holds
    build output, with no package of the repo in it), hold no config cargo reads for a package of the repo."""
    files = {"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN}
    for skipped in ("node_modules/x", "target/debug", ".git/hooks", "a/target/debug/deps"):
        files[f"{skipped}/.cargo/config.toml"] = 'paths = ["x"]\n'
    files["a/Cargo.toml"] = PKG.replace('"app"', '"a"')
    assert verdict(make_repo, files) == TRUSTED


@pytest.mark.parametrize("where", ["crates/target/.cargo/config.toml", "target/.cargo/config.toml", "crates/target/debug/.cargo/config"])
def test_a_config_under_a_directory_called_target_that_sits_beside_no_manifest_is_read(make_repo, where):
    """The review's targetdir (built: a package at `crates/target/` with a `[patch]` in its own `.cargo/config.toml` is built with
    the patch). The source walk reads `crates/target/src/lib.rs` as source, so the config walk reads what is under that `target`;
    on the branch it pruned every directory of the name, and the config was trusted past."""
    files = {
        "crates/target/Cargo.toml": PKG + TOKIO,
        "crates/target/src/main.rs": MAIN,
        where: '[patch.crates-io]\ntokio-macros = { path = "m" }\n',
    }
    if where.startswith("target/"):
        files = {"target/Cargo.toml": PKG + TOKIO, "target/src/main.rs": MAIN, where: files[where]}
    assert verdict(make_repo, files) == UNTRUSTED


def test_a_target_directory_is_pruned_only_where_a_manifest_sits_beside_it(make_repo):
    """`app/target` has the manifest `app/Cargo.toml` beside it, so it is build output; `crates/target` does not, so it is not."""
    files = {
        "app/Cargo.toml": PKG + TOKIO,
        "app/src/main.rs": MAIN,
        "app/target/debug/.cargo/config.toml": 'paths = ["x"]\n',
        "crates/target/inner/.cargo/config.toml": HARMLESS["linker_and_runner"],
    }
    assert rs_modules.cargo_configs(make_repo(files)) == ["crates/target/inner/.cargo/config.toml"]


def verdict_of(repo):
    """The verdict for a repo that is already written (one with a symlink in it)."""
    return verdict(lambda files: repo, {})


def symlink(repo, link, to):
    try:
        (repo / link).parent.mkdir(parents=True, exist_ok=True)
        (repo / link).symlink_to(to, target_is_directory=True)
    except (OSError, NotImplementedError):  # a platform where a link cannot be made (Windows without the right)
        pytest.skip("symlinks are not available here")


PATCH = '[patch.crates-io]\ntokio-macros = { path = "m" }\n'


def test_a_symlinked_cargo_directory_is_read(make_repo):
    """The review's symcargo (built: `.cargo -> cargo-cfg`, a symlink committed in git, with a `[patch]` in cargo-cfg/config.toml:
    `cargo test` compiled the patched tokio-macros). os.walk does not follow a directory link, so the branch never saw the file."""
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, "cargo-cfg/config.toml": PATCH})
    symlink(repo, ".cargo", "cargo-cfg")
    assert rs_modules.cargo_configs(repo) == [".cargo/config.toml"]
    assert verdict_of(repo) == UNTRUSTED


@pytest.mark.parametrize("real", ["-real", "zreal"])
def test_a_symlinked_cargo_directory_is_read_whether_the_directory_it_names_is_walked_before_or_after_it(make_repo, real):
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, f"{real}/config.toml": PATCH})
    symlink(repo, ".cargo", real)
    assert rs_modules.cargo_configs(repo) == [".cargo/config.toml"]


def test_a_symlinked_cargo_directory_that_points_outside_the_repo_is_read(make_repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "config.toml").write_text(PATCH)
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN})
    symlink(repo, ".cargo", outside)
    assert rs_modules.cargo_configs(repo) == [".cargo/config.toml"]
    assert verdict_of(repo) == UNTRUSTED


def test_a_symlinked_config_file_is_read_through_the_link(make_repo):
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, "shared/real.toml": PATCH, ".cargo/placeholder": ""})
    try:
        (repo / ".cargo/config.toml").symlink_to("../shared/real.toml")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    assert verdict_of(repo) == UNTRUSTED


def test_a_config_below_a_symlinked_directory_of_the_repo_is_read(make_repo):
    """A link to a directory of the repo that the plain walk does not enter (here, one under a pruned `.git`) is followed."""
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, ".git/real/deep/.cargo/config.toml": PATCH})
    symlink(repo, "alias", ".git/real")
    assert rs_modules.cargo_configs(repo) == ["alias/deep/.cargo/config.toml"]
    assert verdict_of(repo) == UNTRUSTED


def test_a_symlink_loop_ends_the_walk(make_repo):
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, "a/.cargo/config.toml": HARMLESS["linker_and_runner"]})
    symlink(repo, "a/up", "..")
    symlink(repo, "a/self", ".")
    symlink(repo, "b", "a/up/a")
    assert rs_modules.cargo_configs(repo) == ["a/.cargo/config.toml"]
    assert verdict_of(repo) == TRUSTED


def test_a_symlink_to_a_directory_outside_the_repo_is_not_walked(make_repo, tmp_path_factory):
    """Cargo is run in a package of the repo, which ripple finds in the repo: a directory a link points out of is not one of them
    (and a link to `/` must not make the walk read the machine)."""
    outside = tmp_path_factory.mktemp("elsewhere")
    (outside / "deep/.cargo").mkdir(parents=True)
    (outside / "deep/.cargo/config.toml").write_text(PATCH)
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN})
    symlink(repo, "vendor", outside)
    assert rs_modules.cargo_configs(repo) == []


@pytest.mark.parametrize("where", [".Cargo/config.toml", ".CARGO/CONFIG.TOML", ".cargo/Config.toml", ".Cargo/CONFIG", "src/.Cargo/Config"])
def test_a_config_is_found_whatever_the_case_of_its_names(make_repo, where):
    """Cargo on a case-insensitive file system (macOS, Windows) reads `.Cargo/Config.toml`; ripple reads it everywhere."""
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN, where: PATCH})
    assert rs_modules.cargo_configs(repo) == [where]
    assert verdict_of(repo) == UNTRUSTED


def test_the_fingerprint_covers_exactly_the_files_the_walk_reads(make_repo):
    """The cache is keyed on the files the verdict reads, so a symlinked `.cargo`, a config under a `target` beside no manifest and a
    config spelt in another case are in it, and a config under a `target` beside a manifest is not."""
    from ripple.cache import fingerprint
    from ripple.registry import front_end

    repo = make_repo(
        {
            "Cargo.toml": PKG + TOKIO,
            "src/main.rs": MAIN,
            "cargo-cfg/config.toml": HARMLESS["linker_and_runner"],
            "crates/target/x/.cargo/config": HARMLESS["linker_and_runner"],
            "docs/.Cargo/Config.TOML": HARMLESS["linker_and_runner"],
            "target/debug/.cargo/config.toml": HARMLESS["linker_and_runner"],
        }
    )
    symlink(repo, ".cargo", "cargo-cfg")
    sources = [repo / "src/main.rs"]
    configs = front_end(RUST).config_files(repo, sources)
    assert [c for c in configs if c != "Cargo.toml"] == [".cargo/config.toml", "crates/target/x/.cargo/config", "docs/.Cargo/Config.TOML"]
    assert set(fingerprint(repo, RUST, sources)) == {*configs, "src/main.rs"}


def test_the_configs_are_read_once_per_repo_for_both_what_std_names_and_the_trust_verdict(make_repo, monkeypatch, tmp_path_factory):
    """The configs are read once per repo, whether or not a package names a trusted crate: a config that may change a crate may make `std`
    another one (tests/test_rs_prelude_replaced.py). A package that names no trusted crate gets "does not depend on" as its trust reason, so
    the config's never shows for it (which is why no gate on "the repo asks" is needed); one that names tokio gets the config's."""
    reads = []
    real = rs_modules._config_problems
    monkeypatch.setattr(rs_modules, "_config_problems", lambda repo, configs=None: reads.append(1) or real(repo, configs))
    repo = make_repo({"Cargo.toml": PKG, "src/main.rs": "fn main() {}\n", ".cargo/config.toml": 'paths = ["x"]\n'})
    package = rs_modules.load_workspace(repo, [repo / "src/main.rs"]).packages[""]
    assert reads == [1]
    assert package.trust["tokio"] == "the package does not depend on tokio"
    assert "the key `paths`" in package.std_why
    asking = tmp_path_factory.mktemp("asking")
    (asking / "src").mkdir(parents=True)
    (asking / ".cargo").mkdir()
    (asking / "Cargo.toml").write_text(PKG + TOKIO)
    (asking / "src" / "main.rs").write_text("fn main() {}\n")
    (asking / ".cargo" / "config.toml").write_text('paths = ["x"]\n')
    package = rs_modules.load_workspace(asking, [asking / "src/main.rs"]).packages[""]
    assert package.trust["tokio"] == ".cargo/config.toml sets the key `paths`"


def test_the_real_config_files_of_the_corpora_are_all_harmless():
    """bat, clap, fd and ripgrep each carry one: target rustflags only, or a resolver setting."""
    samples = [HARMLESS["target_rustflags_only"], HARMLESS["target_musl"], HARMLESS["target_cfg_expression_with_link_args"], HARMLESS["resolver_incompatible_rust_versions"]]
    toml = rs_modules._toml()
    assert all(rs_modules._config_table_problem(toml.loads(text)) is None for text in samples)
