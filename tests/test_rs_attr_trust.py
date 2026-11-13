"""The attribute macros ripple trusts: `tokio::main`, `tokio::test` and `serial_test::serial`.

Every item under an attribute macro is read as rewritten, so every call in it is unresolved (ATTR_MACRO). Three macros
are known well enough to leave the body of the function they sit on alone (README, "Attribute macros ripple trusts"):
the published crates wrap it in an async block or a closure of the same function and add nothing the body can see.
That holds only for the crates.io macros, so each site must be proven to be them by the repo: the manifest, the lock,
the cargo config files and how the attribute's path is written. A site the repo does not prove, and every site of
another shape (an impl or trait method, a module, an async `serial`, a second macro beside it), is read exactly as it
was before this rule: the same flags and the same reason.

What is trusted is the body. The name the function declares stays unresolved: old versions of both macro crates drop
its `pub`, so a glob import could no longer bring it in.
"""

import json
import re

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PKG = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
MAIN = "#[tokio::main]\nasync fn main() {\n    helper();\n}\n\nfn helper() {}\n"
SERIAL = "#[cfg(test)]\nmod tests {\n    use serial_test::serial;\n\n    #[test]\n    #[serial]\n    fn t() {\n        super::helper();\n    }\n}\n\npub fn helper() {}\n"
TOKIO, SERIAL_TEST, OTHER = '[dependencies]\ntokio = "1"\n', '[dev-dependencies]\nserial_test = "2"\n', 'other = "1"\n'


def lock(*packages):
    """A Cargo.lock listing (name, source) pairs."""
    return "version = 3\n" + "".join(f'[[package]]\nname = "{n}"\nversion = "1.0.0"\n' + (f'source = "{s}"\n' if s else "") for n, s in packages)


def graph_of(make_repo, files):
    return build_graph(make_repo(files), RUST)


def helper_verdict(graph):
    """What the calls of `helper` came to: "edge" when every one is an edge, else the reasons of the unresolved ones."""
    edges = [e for e in graph.edges if e.kind == "call" and e.callee.endswith("helper")]
    reasons = sorted({u.reason for u in graph.unresolved if u.text.endswith("helper")})
    assert not (edges and reasons), (edges, reasons)
    assert edges or reasons, "no call of helper was read"
    return "edge" if edges else reasons


TRUSTED, UNTRUSTED = "edge", [rs.ATTR_MACRO]

# name: ({path: source}, what the call of `helper` in the attributed function comes to)
CASES = {
    # ---- what a dependency can be
    "plain_version": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN}, TRUSTED),
    "table_version_features": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { version = "1", features = ["full"], default-features = false, optional = true }\n', "src/main.rs": MAIN}, TRUSTED),
    "dotted_table": ({"Cargo.toml": PKG + '[dependencies.tokio]\nversion = "1"\nfeatures = ["rt"]\n', "src/main.rs": MAIN}, TRUSTED),
    "dev_dep_for_test": ({"Cargo.toml": PKG + '[dev-dependencies]\ntokio = "1"\n', "src/lib.rs": "pub fn helper() {}\n", "tests/t.rs": "#[tokio::test]\nasync fn t() {\n    app::helper();\n}\n"}, TRUSTED),
    "workspace_plain": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n[workspace.dependencies]\ntokio = "1"\n', "app/Cargo.toml": PKG + '[dependencies]\ntokio = { workspace = true, features = ["rt"] }\n', "app/src/main.rs": MAIN}, TRUSTED),
    "path_dep": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "vendor/tokio" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "path_and_version": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "vendor/tokio", version = "1" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "git_dep": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { git = "https://example.invalid/tokio" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "git_dep_with_tag": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { git = "https://example.invalid/tokio", tag = "v1" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "alt_registry": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { version = "1", registry = "corp" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "registry_index": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { version = "1", registry-index = "https://example.invalid/index" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "renamed_onto_tokio": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { package = "not-tokio", version = "1" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "renamed_to_itself": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { package = "tokio", version = "1" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "another_key_renamed_onto_tokio": ({"Cargo.toml": PKG + '[dependencies]\ntokio = "1"\nfoo = { package = "tokio", version = "1" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "another_key_renamed_onto_the_macro_crate": ({"Cargo.toml": PKG + '[dependencies]\ntokio = "1"\nfoo = { package = "tokio-macros", version = "2" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "an_unknown_key": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { version = "1", artifact = "bin" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "no_dependency_at_all": ({"Cargo.toml": PKG, "src/main.rs": MAIN}, UNTRUSTED),
    "workspace_path": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n[workspace.dependencies]\ntokio = { path = "fake" }\n', "app/Cargo.toml": PKG + '[dependencies]\ntokio = { workspace = true }\n', "app/src/main.rs": MAIN}, UNTRUSTED),
    "workspace_inherit_with_a_path": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n[workspace.dependencies]\ntokio = "1"\n', "app/Cargo.toml": PKG + '[dependencies]\ntokio = { workspace = true, path = "../fake" }\n', "app/src/main.rs": MAIN}, UNTRUSTED),
    "target_table_path": ({"Cargo.toml": PKG + '[dependencies]\ntokio = "1"\n[target.\'cfg(unix)\'.dependencies]\ntokio = { path = "fake" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "build_dep_path": ({"Cargo.toml": PKG + '[dependencies]\ntokio = "1"\n[build-dependencies]\ntokio = { path = "fake" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "dev_dep_path": ({"Cargo.toml": PKG + '[dependencies]\ntokio = "1"\n[dev-dependencies]\ntokio = { path = "fake" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "legacy_dev_underscore_path": ({"Cargo.toml": PKG + '[dependencies]\ntokio = "1"\n[dev_dependencies]\ntokio = { path = "fake" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "serial_hyphen_key": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial-test = "2"\n', "src/lib.rs": SERIAL}, UNTRUSTED),
    # ---- what a patch, a replace, a config file or a lock can say
    "patch_macro_crate": ({"Cargo.toml": PKG + TOKIO + '[patch.crates-io]\ntokio-macros = { path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "patch_tokio_git": ({"Cargo.toml": PKG + TOKIO + '[patch.crates-io]\ntokio = { git = "https://example.invalid/t" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "patch_of_another_crate_refuses_too": ({"Cargo.toml": PKG + TOKIO + '[patch.crates-io]\nsomething-else = { path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "patch_serial_derive": ({"Cargo.toml": PKG + SERIAL_TEST + '[patch.crates-io]\nserial_test_derive = { path = "m" }\n', "src/lib.rs": SERIAL}, UNTRUSTED),
    "patch_at_the_workspace_root": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n[patch.crates-io]\ntokio-macros = { path = "m" }\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "replace_macro_crate": ({"Cargo.toml": PKG + TOKIO + '[replace]\n"tokio-macros:2.1.0" = { path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "replace_serial_derive": ({"Cargo.toml": PKG + SERIAL_TEST + '[replace]\n"serial_test_derive:2.0.0" = { path = "m" }\n', "src/lib.rs": SERIAL}, UNTRUSTED),
    "config_paths": ({"Cargo.toml": PKG + TOKIO, ".cargo/config.toml": 'paths = ["../tokio-macros"]\n', "src/main.rs": MAIN}, UNTRUSTED),
    "config_source_vendor": ({"Cargo.toml": PKG + TOKIO, ".cargo/config.toml": '[source.crates-io]\nreplace-with = "v"\n[source.v]\ndirectory = "vendor"\n', "src/main.rs": MAIN}, UNTRUSTED),
    "config_patch_in_the_old_file_name": ({"Cargo.toml": PKG + TOKIO, ".cargo/config": '[patch.crates-io]\ntokio-macros = { path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "config_lockfile_path": ({"Cargo.toml": PKG + TOKIO, ".cargo/config.toml": '[resolver]\nlockfile-path = "elsewhere/Cargo.lock"\n', "src/main.rs": MAIN}, UNTRUSTED),
    "config_lockfile_path_dotted": ({"Cargo.toml": PKG + TOKIO, ".cargo/config.toml": 'resolver.lockfile-path = "elsewhere/Cargo.lock"\n', "src/main.rs": MAIN}, UNTRUSTED),
    "config_include": ({"Cargo.toml": PKG + TOKIO, ".cargo/config.toml": 'include = ["more.toml"]\n', "src/main.rs": MAIN}, UNTRUSTED),
    "config_unreadable": ({"Cargo.toml": PKG + TOKIO, ".cargo/config.toml": "this is [not toml\n", "src/main.rs": MAIN}, UNTRUSTED),
    "config_above_the_package": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n', ".cargo/config.toml": 'paths = ["x"]\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "config_in_the_package_of_a_workspace": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n', "app/.cargo/config.toml": 'paths = ["x"]\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "config_above_a_package_with_no_workspace": ({".cargo/config.toml": 'paths = ["x"]\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "config_rustflags_only": ({"Cargo.toml": PKG + TOKIO, ".cargo/config.toml": '[target.x86_64-pc-windows-msvc]\nrustflags = ["-C", "target-feature=+crt-static"]\n[build]\njobs = 2\n', "src/main.rs": MAIN}, TRUSTED),
    "lock_registry": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", CRATES_IO)), "src/main.rs": MAIN}, TRUSTED),
    "lock_sparse_registry": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio", "sparse+https://index.crates.io/"), ("tokio-macros", "sparse+https://index.crates.io/")), "src/main.rs": MAIN}, TRUSTED),
    "lock_git_macros": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", "git+https://example.invalid/m#abc")), "src/main.rs": MAIN}, UNTRUSTED),
    "lock_git_tokio": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio", "git+https://example.invalid/t#abc"), ("tokio-macros", CRATES_IO)), "src/main.rs": MAIN}, UNTRUSTED),
    "lock_path_source_is_no_source": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", None)), "src/main.rs": MAIN}, UNTRUSTED),
    "lock_alt_registry": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", "registry+https://example.invalid/index")), "src/main.rs": MAIN}, UNTRUSTED),
    "lock_missing_macros": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio", CRATES_IO)), "src/main.rs": MAIN}, UNTRUSTED),
    "lock_missing_the_crate": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio-macros", CRATES_IO)), "src/main.rs": MAIN}, UNTRUSTED),
    "lock_one_of_two_versions_from_git": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", CRATES_IO), ("tokio-macros", "git+https://example.invalid/m#abc")), "src/main.rs": MAIN}, UNTRUSTED),
    "lock_unreadable": ({"Cargo.toml": PKG + TOKIO, "Cargo.lock": "[[package\n", "src/main.rs": MAIN}, UNTRUSTED),
    "lock_serial_plain": ({"Cargo.toml": PKG + SERIAL_TEST, "Cargo.lock": lock(("serial_test", CRATES_IO), ("serial_test_derive", CRATES_IO)), "src/lib.rs": SERIAL}, TRUSTED),
    "lock_serial_derive_from_git": ({"Cargo.toml": PKG + SERIAL_TEST, "Cargo.lock": lock(("serial_test", CRATES_IO), ("serial_test_derive", "git+https://example.invalid/d#abc")), "src/lib.rs": SERIAL}, UNTRUSTED),
    "lock_serial_missing_derive": ({"Cargo.toml": PKG + SERIAL_TEST, "Cargo.lock": lock(("serial_test", CRATES_IO)), "src/lib.rs": SERIAL}, UNTRUSTED),
    "lock_at_the_workspace_root": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n', "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", "git+https://example.invalid/m#abc")), "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "member_named_tokio_macros": ({"Cargo.toml": '[workspace]\nmembers = ["app", "tokio-macros"]\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN, "tokio-macros/Cargo.toml": '[package]\nname = "tokio-macros"\nversion = "2.1.0"\nedition = "2021"\n[lib]\nproc-macro = true\n', "tokio-macros/src/lib.rs": ""}, UNTRUSTED),
    "member_named_serial_test_derive": ({"Cargo.toml": '[workspace]\nmembers = ["app", "d"]\n', "app/Cargo.toml": PKG + SERIAL_TEST, "app/src/lib.rs": SERIAL, "d/Cargo.toml": '[package]\nname = "serial-test-derive"\nversion = "2.0.0"\nedition = "2021"\n[lib]\nproc-macro = true\n', "d/src/lib.rs": ""}, UNTRUSTED),
    "repo_is_tokio": ({"Cargo.toml": '[package]\nname = "tokio"\nversion = "1.0.0"\nedition = "2021"\n', "src/lib.rs": "pub fn helper() {}\n", "examples/e.rs": "#[tokio::main]\nasync fn main() {\n    tokio::helper();\n}\n"}, UNTRUSTED),
    "edition_2015": ({"Cargo.toml": PKG.replace('edition = "2021"\n', "") + TOKIO, "src/main.rs": MAIN}, UNTRUSTED),
    "edition_2015_written": ({"Cargo.toml": PKG.replace("2021", "2015") + TOKIO, "src/main.rs": MAIN}, UNTRUSTED),
    "edition_2018": ({"Cargo.toml": PKG.replace("2021", "2018") + TOKIO, "src/main.rs": MAIN}, TRUSTED),
    # ---- how the attribute is written, and what its first name is
    "local_mod_tokio": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "mod tokio {\n    pub use other::main;\n}\n" + MAIN}, UNTRUSTED),
    "local_empty_mod_tokio": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "mod tokio {}\n" + MAIN}, UNTRUSTED),
    "global_path_beats_local_mod": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "mod tokio {\n    pub use other::main;\n}\n" + MAIN.replace("#[tokio::main]", "#[::tokio::main]")}, TRUSTED),
    "global_path_with_an_extern_alias": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "extern crate other as tokio;\n" + MAIN.replace("#[tokio::main]", "#[::tokio::main]")}, UNTRUSTED),
    "extern_crate_alias": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "extern crate other as tokio;\n" + MAIN}, UNTRUSTED),
    "extern_crate_plain": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "extern crate tokio;\n" + MAIN}, UNTRUSTED),  # stricter than needed: tokio is then an item of the module, not the crate
    "use_alias": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "use other as tokio;\n" + MAIN}, UNTRUSTED),
    "cfg_use_alias": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": '#[cfg(feature = "x")]\nuse other as tokio;\n' + MAIN}, UNTRUSTED),
    "outside_glob": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "use other::*;\n" + MAIN}, UNTRUSTED),
    "module_macro_call": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "other::make!();\n" + MAIN}, UNTRUSTED),
    "module_std_macro_call": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "const N: usize = concat!(\"a\").len();\nfn s() { println!(\"x\"); }\n" + MAIN}, TRUSTED),
    "tokio_used_by_a_different_path_is_not_listed": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "use tokio::main;\n" + MAIN.replace("#[tokio::main]", "#[main]")}, UNTRUSTED),
    "tokio_reexported_under_another_name": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "pub use tokio as rt;\n" + MAIN.replace("#[tokio::main]", "#[rt::main]")}, UNTRUSTED),
    "raw_identifier": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "#[r#tokio::main]")}, UNTRUSTED),
    "tokio_other_macro_is_not_listed": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "#[tokio::select]")}, UNTRUSTED),
    "flavor_and_crate_args": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", '#[tokio::main(flavor = "current_thread", crate = "tokio", start_paused = true)]')}, TRUSTED),
    "tokio_test_form": ({"Cargo.toml": PKG + TOKIO, "src/lib.rs": "#[cfg(test)]\nmod t {\n    #[tokio::test]\n    async fn t() {\n        super::helper();\n    }\n}\npub fn helper() {}\n"}, TRUSTED),
    "tokio_test_global_path": ({"Cargo.toml": PKG + TOKIO, "src/lib.rs": "#[cfg(test)]\nmod t {\n    #[::tokio::test]\n    async fn t() {\n        super::helper();\n    }\n}\npub fn helper() {}\n"}, TRUSTED),
    "tokio_cfg_attr": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "#[cfg_attr(feature = \"rt\", tokio::main)]")}, TRUSTED),
    "tokio_cfg_attr_with_another_macro": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": MAIN.replace("#[tokio::main]", "#[cfg_attr(feature = \"rt\", tokio::main, other::rewrite)]")}, UNTRUSTED),
    # ---- `serial` is a name a use brings in
    "serial_plain": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL}, TRUSTED),
    "serial_path_form": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    use serial_test::serial;\n", "").replace("#[serial]", "#[serial_test::serial]")}, TRUSTED),
    "serial_global_path_form": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    use serial_test::serial;\n", "").replace("#[serial]", "#[::serial_test::serial]")}, TRUSTED),
    "serial_path_form_with_a_local_serial_test": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    use serial_test::serial;\n", "    mod serial_test {}\n").replace("#[serial]", "#[serial_test::serial]")}, UNTRUSTED),
    "serial_path_form_with_a_local_serial_test_in_another_module": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": "mod serial_test {}\n" + SERIAL.replace("    use serial_test::serial;\n", "").replace("#[serial]", "#[serial_test::serial]")}, TRUSTED),
    "serial_global_use": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use ::serial_test::serial;")}, TRUSTED),
    "serial_key_args": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\n', "src/lib.rs": SERIAL.replace("#[serial]", '#[serial(env, path => "/tmp/x")]')}, TRUSTED),
    "serial_other_import": ({"Cargo.toml": PKG + SERIAL_TEST + OTHER, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use other::serial;")}, UNTRUSTED),
    "serial_renamed_import": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use serial_test::parallel as serial;")}, UNTRUSTED),
    "serial_file_serial_is_not_serial": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use serial_test::file_serial as serial;")}, UNTRUSTED),
    "serial_glob_only": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use serial_test::*;")}, UNTRUSTED),
    "serial_no_import_at_all": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    use serial_test::serial;\n", "")}, UNTRUSTED),
    "serial_import_in_parent_only": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": "use serial_test::serial;\n" + SERIAL.replace("    use serial_test::serial;\n", "")}, UNTRUSTED),
    "serial_imported_twice": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    use serial_test::serial;\n", "    use serial_test::serial;\n    use serial_test::serial;\n")}, UNTRUSTED),
    "serial_imported_by_a_list": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use serial_test::{serial};")}, TRUSTED),
    "serial_macro_rules_named_serial": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": "macro_rules! serial { () => {} }\n" + SERIAL}, UNTRUSTED),
    "serial_local_mod_serial_test": ({"Cargo.toml": PKG + SERIAL_TEST + OTHER, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use crate::serial_test::serial;") + "mod serial_test {\n    pub use other::serial;\n}\n"}, UNTRUSTED),
    "serial_serial_test_is_a_local_mod_in_scope": ({"Cargo.toml": PKG + SERIAL_TEST + OTHER, "src/lib.rs": "mod serial_test {\n    pub use other::serial;\n}\n" + SERIAL.replace("    use serial_test::serial;\n", "    use super::serial_test::serial;\n")}, UNTRUSTED),
    "serial_cfg_attr": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("#[serial]", "#[cfg_attr(test, serial)]")}, TRUSTED),
    "serial_cfg_use": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    use serial_test::serial;", "    #[cfg(unix)]\n    use serial_test::serial;")}, UNTRUSTED),
    "serial_block_glob_between": ({"Cargo.toml": PKG + SERIAL_TEST + OTHER, "src/lib.rs": "use serial_test::serial;\npub fn outer() {\n    use other::*;\n    #[serial]\n    fn t() {\n        helper();\n    }\n    t();\n}\npub fn helper() {}\n"}, UNTRUSTED),
    "serial_block_use_inside_the_function": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": "pub fn outer() {\n    use serial_test::serial;\n    #[serial]\n    fn t() {\n        helper();\n    }\n    t();\n}\npub fn helper() {}\n"}, TRUSTED),
    "serial_macro_in_the_block_before_the_function": ({"Cargo.toml": PKG + SERIAL_TEST + OTHER, "src/lib.rs": "use serial_test::serial;\npub fn outer() {\n    other::make!();\n    #[serial]\n    fn t() {\n        helper();\n    }\n    t();\n}\npub fn helper() {}\n"}, UNTRUSTED),
    "serial_macro_use_extern_crate": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": "#[macro_use]\nextern crate serial_test;\n" + SERIAL.replace("    use serial_test::serial;\n", "")}, UNTRUSTED),
    # ---- the shapes the rule does not cover
    "on_impl_method": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "struct S;\nimpl S {\n    #[tokio::main]\n    async fn run() {\n        helper();\n    }\n}\nfn main() {}\nfn helper() {}\n"}, UNTRUSTED),
    "on_trait_impl_method": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "struct S;\ntrait T { fn run(); }\nimpl T for S {\n    #[tokio::main]\n    async fn run() {\n        helper();\n    }\n}\nfn main() {}\nfn helper() {}\n"}, UNTRUSTED),
    "on_trait_default_method": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "trait T {\n    #[tokio::main]\n    async fn run() {\n        helper();\n    }\n}\nfn main() {}\nfn helper() {}\n"}, UNTRUSTED),
    "serial_on_impl_method": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": "use serial_test::serial;\nstruct S;\nimpl S {\n    #[serial]\n    fn run() {\n        helper();\n    }\n}\npub fn helper() {}\n"}, UNTRUSTED),
    "on_a_struct": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "#[tokio::main]\nstruct S;\nfn main() { helper(); }\n"}, [rs.MACRO_MAY_MAKE]),
    "on_a_module": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "#[tokio::main]\nmod m {\n    pub fn run() { super::helper(); }\n}\nfn main() {}\nfn helper() {}\n"}, UNTRUSTED),
    "serial_on_mod": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\n', "src/lib.rs": "use serial_test::serial;\n#[serial]\nmod tests {\n    #[test]\n    fn t() {\n        super::helper();\n    }\n}\npub fn helper() {}\n"}, UNTRUSTED),
    "serial_async": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\ntokio = "1"\n', "src/lib.rs": SERIAL.replace("    #[test]\n    #[serial]\n    fn t()", "    #[tokio::test]\n    #[serial]\n    async fn t()")}, UNTRUSTED),
    "serial_async_alone": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    fn t()", "    async fn t()")}, UNTRUSTED),
    "serial_inner_attrs": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\n', "src/lib.rs": SERIAL.replace("#[serial]", "#[serial(inner_attrs = [other::rewrite])]")}, UNTRUSTED),
    "serial_inner_attrs_any_case": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\n', "src/lib.rs": SERIAL.replace("#[serial]", "#[serial(Inner_Attrs = [other::rewrite])]")}, UNTRUSTED),
    "serial_inner_attrs_path_form": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\n', "src/lib.rs": SERIAL.replace("    use serial_test::serial;\n", "").replace("#[serial]", "#[serial_test::serial(inner_attrs = [other::rewrite])]")}, UNTRUSTED),
    "serial_inner_attrs_in_cfg_attr": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\n', "src/lib.rs": SERIAL.replace("#[serial]", "#[cfg_attr(test, serial(inner_attrs = [other::rewrite]))]")}, UNTRUSTED),
    "beside_other_macro": ({"Cargo.toml": PKG + TOKIO + 'tracing = "1"\n', "src/main.rs": MAIN.replace("#[tokio::main]", "#[tokio::main]\n#[tracing::instrument]")}, UNTRUSTED),
    "beside_other_macro_first": ({"Cargo.toml": PKG + TOKIO + 'tracing = "1"\n', "src/main.rs": MAIN.replace("#[tokio::main]", "#[tracing::instrument]\n#[tokio::main]")}, UNTRUSTED),
    "beside_a_plain_name_macro": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "use other::rewrite;\n" + MAIN.replace("#[tokio::main]", "#[tokio::main]\n#[rewrite]")}, UNTRUSTED),
    "serial_beside_other_macro": ({"Cargo.toml": PKG + SERIAL_TEST + OTHER, "src/lib.rs": SERIAL.replace("    #[serial]\n", "    #[serial]\n    #[other::rewrite]\n")}, UNTRUSTED),
    "beside_builtin_attributes_it_is_trusted": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "#[allow(unused)]\n#[doc = \"x\"]\n#[tokio::main]\n#[inline]")}, TRUSTED),
    "beside_a_cfg_it_is_trusted": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "#[cfg(unix)]\n#[tokio::main]")}, TRUSTED),
    "both_trusted_macros_on_a_sync_function": ({"Cargo.toml": PKG + TOKIO + SERIAL_TEST.replace("[dev-dependencies]\n", ""), "src/main.rs": "use serial_test::serial;\n" + MAIN.replace("#[tokio::main]\nasync fn", "#[tokio::main]\n#[serial]\nfn")}, TRUSTED),
    "inside_an_attribute_macro_module": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "#[other::rewrite]\nmod m {\n    #[tokio::main]\n    pub async fn run() { super::helper(); }\n}\nfn main() {}\nfn helper() {}\n"}, UNTRUSTED),
    "inside_an_attribute_macro_fn": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "#[other::rewrite]\nfn outer() {\n    #[tokio::main]\n    async fn run() { helper(); }\n}\nfn main() {}\nfn helper() {}\n"}, UNTRUSTED),
    "nested_in_a_trusted_function": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "#[tokio::main]\nasync fn main() {\n    #[tokio::main]\n    async fn inner() { helper(); }\n    helper();\n}\nfn helper() {}\n"}, TRUSTED),
    "nested_in_a_refused_function": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "t" }\n', "src/main.rs": "#[tokio::main]\nasync fn main() {\n    #[tokio::main]\n    async fn inner() { helper(); }\n    helper();\n}\nfn helper() {}\n"}, UNTRUSTED),
    # ---- a comment is not a token: it may sit between any two of them
    "comment_in_attr": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "#[tokio /* a */ :: // b\n main]")}, TRUSTED),
    "comment_in_attr_of_a_refused_site": ({"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "t" }\n', "src/main.rs": MAIN.replace("#[tokio::main]", "#[tokio /* a */ :: // b\n main]")}, UNTRUSTED),
    "comment_around_the_hash_and_brackets": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "# /* a */ [ /* b */ tokio::main /* c */ ] // d")}, TRUSTED),
    "comment_in_the_args": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", '#[tokio::main(/* x */ flavor = "current_thread" // y\n)]')}, TRUSTED),
    "comment_between_attribute_and_item": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]\n", "#[tokio::main]\n// c\n/* d */ ")}, TRUSTED),
    "comment_in_cfg_attr": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "#[cfg_attr(/* a */ test, /* b */ tokio /* c */ :: /* d */ main)]")}, TRUSTED),
    "comment_in_the_serial_path": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    use serial_test::serial;\n", "").replace("#[serial]", "#[serial_test /* c */ :: serial]")}, TRUSTED),
    "comment_in_the_serial_use": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use serial_test /* c */ :: // d\n serial /* e */;")}, TRUSTED),
    "comment_in_the_serial_args": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("#[serial]", "#[serial(/* c */ key // d\n)]")}, TRUSTED),
    "comment_that_says_inner_attrs": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\n', "src/lib.rs": SERIAL.replace("#[serial]", "#[serial(/* inner_attrs = [x] */ key)]")}, TRUSTED),
    "comment_in_inner_attrs_argument": ({"Cargo.toml": PKG + '[dev-dependencies]\nserial_test = "3"\n', "src/lib.rs": SERIAL.replace("#[serial]", "#[serial(inner_attrs /* c */ = [other::rewrite])]")}, UNTRUSTED),
    "comment_beside_async_serial": ({"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": SERIAL.replace("    fn t()", "    /* c */ async /* d */ fn t()")}, UNTRUSTED),
    "comment_in_an_impl_method_path": ({"Cargo.toml": PKG + TOKIO, "src/main.rs": "struct S;\nimpl S {\n    #[tokio /* c */ :: main]\n    async fn run() { helper(); }\n}\nfn main() {}\nfn helper() {}\n"}, UNTRUSTED),
    # ---- any `[replace]` and any `[patch]` entry refuses, in any spelling of its key (review F2)
    "replace_key_with_at": ({"Cargo.toml": PKG + TOKIO + '[replace]\n"tokio-macros@2.1.0" = { path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "replace_key_url": ({"Cargo.toml": PKG + TOKIO + '[replace]\n"https://github.com/rust-lang/crates.io-index#tokio-macros@2.1.0" = { path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "replace_of_another_crate": ({"Cargo.toml": PKG + TOKIO + '[replace]\n"something-else:1.0.0" = { path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "replace_serial_derive_key_with_at": ({"Cargo.toml": PKG + SERIAL_TEST + '[replace]\n"serial_test_derive@2.0.0" = { path = "m" }\n', "src/lib.rs": SERIAL}, UNTRUSTED),
    "patch_renamed_onto_the_macro_crate": ({"Cargo.toml": PKG + TOKIO + '[patch.crates-io]\nfoo = { package = "tokio-macros", path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "patch_of_quote": ({"Cargo.toml": PKG + TOKIO + '[patch.crates-io]\nquote = { path = "q" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "patch_of_syn": ({"Cargo.toml": PKG + TOKIO + '[patch.crates-io]\nsyn = { git = "https://example.invalid/syn" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "patch_of_proc_macro2": ({"Cargo.toml": PKG + TOKIO + '[patch.crates-io]\nproc-macro2 = { path = "p" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "patch_in_another_registry_table": ({"Cargo.toml": PKG + TOKIO + "[patch.'https://example.invalid/index']\nx = { path = \"m\" }\n", "src/main.rs": MAIN}, UNTRUSTED),
    "patch_written_with_a_dotted_key": ({"Cargo.toml": PKG + TOKIO + '[patch]\ncrates-io.x = { path = "m" }\n', "src/main.rs": MAIN}, UNTRUSTED),
    "patch_of_the_serial_crate_by_a_url": ({"Cargo.toml": PKG + SERIAL_TEST + "[patch.'https://github.com/rust-lang/crates.io-index']\nserial_test = { path = \"m\" }\n", "src/lib.rs": SERIAL}, UNTRUSTED),
    "patch_with_no_entry_is_no_matter": ({"Cargo.toml": PKG + TOKIO + "[patch.crates-io]\n", "src/main.rs": MAIN}, TRUSTED),
    "patch_in_the_manifest_of_a_member": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n', "app/Cargo.toml": PKG + TOKIO + '[patch.crates-io]\nx = { path = "m" }\n', "app/src/main.rs": MAIN}, UNTRUSTED),
    "patch_at_the_root_of_an_unrelated_crate": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n[patch.crates-io]\nx = { path = "m" }\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "patch_in_a_package_above_the_package": ({"Cargo.toml": '[workspace]\nmembers = ["a/b"]\n', "a/Cargo.toml": PKG.replace('"app"', '"a"') + '[patch.crates-io]\nx = { path = "m" }\n', "a/src/lib.rs": "", "a/b/Cargo.toml": PKG + TOKIO, "a/b/src/main.rs": MAIN}, UNTRUSTED),
    # ---- a Cargo.toml that cannot be read at or above the package refuses it (review F3)
    "root_manifest_unreadable": ({"Cargo.toml": '[workspace\nmembers = ["app"]\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "root_manifest_unreadable_with_a_lock_beside_it": ({"Cargo.toml": '[workspace\nmembers = ["app"]\n', "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", None)), "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "root_manifest_in_toml_1_1": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\nresolver = "2"\n[patch.crates-io]\ntokio-macros = { path = "../x", }\n', "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", None)), "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "manifest_unreadable_between_the_package_and_the_root": ({"Cargo.toml": '[workspace]\nmembers = ["a/b"]\n', "a/Cargo.toml": "[package\n", "a/src/lib.rs": "", "a/b/Cargo.toml": PKG + TOKIO, "a/b/src/main.rs": MAIN}, UNTRUSTED),
    "manifest_unreadable_beside_the_package_is_no_matter": ({"z/Cargo.toml": "[package\n", "z/src/lib.rs": "", "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, TRUSTED),
    # ---- `package.workspace` names a manifest ripple has not read (review F5)
    "workspace_named_outside_the_sources": ({"ws/Cargo.toml": '[workspace]\nmembers = ["../app"]\n', "ws/Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", None)), "app/Cargo.toml": PKG + 'workspace = "../ws"\n' + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "workspace_named_outside_the_sources_with_nothing_to_find": ({"ws/Cargo.toml": '[workspace]\nmembers = ["../app"]\n', "app/Cargo.toml": PKG + 'workspace = "../ws"\n' + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "workspace_named_that_is_not_there": ({"app/Cargo.toml": PKG + 'workspace = "../nowhere"\n' + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "workspace_named_that_is_not_a_workspace": ({"ws/Cargo.toml": PKG.replace('"app"', '"ws"'), "ws/src/lib.rs": "", "app/Cargo.toml": PKG + 'workspace = "../ws"\n' + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "workspace_named_that_is_not_a_string": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n', "app/Cargo.toml": PKG + "workspace = true\n" + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "workspace_named_above_and_read": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n', "app/Cargo.toml": PKG + 'workspace = ".."\n' + TOKIO, "app/src/main.rs": MAIN}, TRUSTED),
    # ---- a package its workspace excludes is a workspace of its own, with its own lock (review, unverified item 3)
    "excluded_member_whose_own_lock_takes_the_macros_from_git": ({"Cargo.toml": '[workspace]\nmembers = ["lib1"]\nexclude = ["app"]\n', "Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", CRATES_IO)), "app/Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", "git+https://example.invalid/m#abc")), "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "excluded_member_with_a_lock_that_is_fine": ({"Cargo.toml": '[workspace]\nmembers = ["lib1"]\nexclude = ["app"]\n', "app/Cargo.lock": lock(("tokio", CRATES_IO), ("tokio-macros", CRATES_IO)), "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "excluded_by_a_directory_above_it": ({"Cargo.toml": '[workspace]\nexclude = ["crates"]\n', "crates/app/Cargo.toml": PKG + TOKIO, "crates/app/src/main.rs": MAIN}, UNTRUSTED),
    "excluded_by_a_glob": ({"Cargo.toml": '[workspace]\nexclude = ["ap*"]\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "excluded_by_something_that_is_not_a_list": ({"Cargo.toml": '[workspace]\nexclude = "app"\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "another_directory_excluded_is_no_matter": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\nexclude = ["other", "crates/x"]\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, TRUSTED),
    "patch_in_a_form_cargo_rejects": ({"Cargo.toml": "patch = 1\n" + PKG + TOKIO, "src/main.rs": MAIN}, UNTRUSTED),
    "patch_registry_in_a_form_cargo_rejects": ({"Cargo.toml": PKG + TOKIO + "[patch]\ncrates-io = 1\n", "src/main.rs": MAIN}, UNTRUSTED),
    "patch_in_a_named_root_that_is_no_ancestor": ({"ws/Cargo.toml": '[workspace]\nmembers = ["../app"]\n[patch.crates-io]\nx = { path = "m" }\n', "ws/src/lib.rs": "", "app/Cargo.toml": PKG + 'workspace = "../ws"\n' + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "workspace_named_beside_and_read": ({"ws/Cargo.toml": '[workspace]\nmembers = ["../app"]\n', "ws/src/lib.rs": "", "app/Cargo.toml": PKG + 'workspace = "../ws"\n' + TOKIO, "app/src/main.rs": MAIN}, TRUSTED),
    "workspace_named_by_a_number_that_is_a_directory_with_a_workspace": ({"Cargo.toml": '[workspace]\nmembers = ["app"]\n', "app/Cargo.toml": PKG + "workspace = 1\n" + TOKIO, "app/src/main.rs": MAIN, "app/1/Cargo.toml": "[workspace]\n", "app/1/src/lib.rs": ""}, UNTRUSTED),
    "excluded_by_the_dot": ({"Cargo.toml": '[workspace]\nexclude = ["."]\n', "app/Cargo.toml": PKG + TOKIO, "app/src/main.rs": MAIN}, UNTRUSTED),
    "a_package_that_is_its_own_workspace_is_not_excluded_by_its_own_exclude": ({"Cargo.toml": PKG + TOKIO + '[workspace]\nexclude = ["examples/*", "target"]\n', "src/main.rs": MAIN}, TRUSTED),
    # ---- the checks that were only defensive, or that nothing reached (review F6)
    "serial_extern_crate_alias": ({"Cargo.toml": PKG + SERIAL_TEST + OTHER, "src/lib.rs": "extern crate other as serial_test;\n" + SERIAL}, UNTRUSTED),
    "edition_2015_global_path": ({"Cargo.toml": PKG.replace('edition = "2021"\n', "") + TOKIO, "src/main.rs": MAIN.replace("#[tokio::main]", "#[::tokio::main]")}, UNTRUSTED),
    "edition_2015_global_serial_use": ({"Cargo.toml": PKG.replace('edition = "2021"\n', "") + SERIAL_TEST, "src/lib.rs": SERIAL.replace("use serial_test::serial;", "use ::serial_test::serial;")}, UNTRUSTED),
    "a_trusted_head_beside_another_macro_with_a_glob_that_could_hold_a_name": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "use util::*;\n#[tokio::main]\n#[other::trace]\nasync fn main() {}\nfn run() { helper(); }\nmod util { pub fn helper() {} }\n"}, [rs.MACRO_MAY_MAKE]),  # `run` is beside the macro: a name it brings in by a glob may be one the macro made
    "a_trusted_head_beside_a_plain_name_macro_with_a_glob_that_could_hold_a_name": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "use util::*;\nuse other::thing;\n#[tokio::main]\n#[thing]\nasync fn main() {}\nfn run() { helper(); }\nmod util { pub fn helper() {} }\n"}, [rs.MACRO_MAY_MAKE]),  # `run` is beside the macro: a name it brings in by a glob may be one the macro made
    "a_trusted_site_beside_a_derive_whose_helper_is_a_macro_the_crate_uses": ({"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": "use other::thing;\n#[derive(Foo)]\n#[thing]\nstruct S;\n" + MAIN}, UNTRUSTED),
}


@pytest.mark.parametrize("name", CASES)
def test_the_call_in_an_attributed_function_is_an_edge_exactly_when_the_repo_proves_the_macro(make_repo, name):
    files, expected = CASES[name]
    assert helper_verdict(graph_of(make_repo, files)) == expected


# ---- the name the function declares stays unresolved


def test_a_trusted_function_loses_nothing_but_its_name(make_repo):
    src = MAIN + "fn run() { main(); }\n"
    g = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO, "src/main.rs": src})
    assert ("src/main:main", "src/main:helper") in {(e.caller, e.callee) for e in g.edges}
    assert {(u.caller, u.text): u.reason for u in g.unresolved} == {("src/main:run", "main"): rs.ATTR_MACRO}


def test_a_glob_import_does_not_bring_in_the_name_an_old_macro_dropped_the_pub_of(make_repo):
    """tokio-macros up to 0.2.0 and serial_test_derive up to 0.9 rebuild the function without its `pub`, so a glob import of
    the module would not find it and a different item of the name would be called. The name stays unresolved whatever
    the version, so the call is never an edge to either."""
    src = "mod util {\n    pub fn t() {}\n}\nuse util::*;\n#[tokio::test]\nasync fn t() {}\nfn other() {\n    t();\n}\n"
    g = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO, "src/lib.rs": src})
    assert not g.edges
    assert {(u.caller, u.text): u.reason for u in g.unresolved} == {("src/lib:other", "t"): rs.ATTR_MACRO}


def test_the_name_of_a_trusted_serial_function_is_unresolved_too(make_repo):
    src = "use serial_test::serial;\n#[serial]\nfn t() {}\nfn other() {\n    t();\n}\n"
    g = graph_of(make_repo, {"Cargo.toml": PKG + SERIAL_TEST, "src/lib.rs": src})
    assert {(u.caller, u.text): u.reason for u in g.unresolved} == {("src/lib:other", "t"): rs.ATTR_MACRO}


# ---- async `serial` puts a function beside the test (serial_test_derive 0.10 to 3.1): the scope is not trusted either


def test_the_function_async_serial_puts_beside_the_test_wins_over_a_glob_import_so_both_stay_unresolved(make_repo):
    src = (
        "mod util {\n    pub fn _t_internal() -> u8 { 1 }\n    pub fn helper() {}\n}\n"
        "use util::*;\nuse serial_test::serial;\n"
        "#[tokio::test]\n#[serial]\nasync fn t() {\n    helper();\n}\n"
        "fn other() {\n    _t_internal();\n}\n"
    )
    g = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO + SERIAL_TEST.replace("[dev-dependencies]\n", ""), "src/lib.rs": src})
    assert not g.edges
    assert {(u.caller, u.text): u.reason for u in g.unresolved} == {
        ("src/lib:t", "helper"): rs.ATTR_MACRO,
        ("src/lib:other", "_t_internal"): rs.MACRO_MAY_MAKE,
    }


# ---- what is inside a trusted function is read as the body of any function


def test_the_items_a_trusted_function_declares_are_read_like_those_of_an_ordinary_one(make_repo):
    src = (
        "#[tokio::main]\nasync fn main() {\n    struct S;\n    impl S {\n        fn f() {}\n    }\n    fn nested() {}\n"
        "    S::f();\n    nested();\n    helper();\n}\nfn helper() {}\n"
    )
    g = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO, "src/main.rs": src})
    assert {(e.caller, e.callee) for e in g.edges} == {
        ("src/main:main", "src/main:main::S::f"),
        ("src/main:main", "src/main:main::nested"),
        ("src/main:main", "src/main:helper"),
    }
    assert not g.unresolved


def test_the_same_function_under_a_refused_attribute_has_all_of_it_unresolved(make_repo):
    src = (
        "#[tokio::main]\nasync fn main() {\n    struct S;\n    impl S {\n        fn f() {}\n    }\n    fn nested() {}\n"
        "    S::f();\n    nested();\n    helper();\n}\nfn helper() {}\n"
    )
    g = graph_of(make_repo, {"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "t" }\n', "src/main.rs": src})
    assert not g.edges
    assert {(u.text, u.reason) for u in g.unresolved} == {("S::f", rs.ATTR_MACRO), ("nested", rs.ATTR_MACRO), ("helper", rs.ATTR_MACRO)}


IMPL_IN_BODY = "struct S;\n#[tokio::main]\nasync fn main() {\n    impl S {\n        fn f() {}\n    }\n}\nfn run() {\n    S::f();\n}\n"


def test_an_impl_inside_a_trusted_function_applies_to_the_whole_crate_and_is_read(make_repo):
    g = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO, "src/main.rs": IMPL_IN_BODY})
    assert {(e.caller, e.callee) for e in g.edges} == {("src/main:run", "src/main:main::S::f")}


def test_an_impl_inside_a_refused_function_is_one_an_attribute_macro_may_have_rewritten(make_repo):
    g = graph_of(make_repo, {"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "t" }\n', "src/main.rs": IMPL_IN_BODY})
    assert not g.edges
    assert {(u.caller, u.text): u.reason for u in g.unresolved} == {("src/main:run", "S::f"): rs.ATTR_MACRO}


def test_a_trusted_function_in_the_body_of_an_impl_method_is_decided_too(make_repo):
    """The impls of a body are read after the first pass that decides, so a site found only then is decided by the second."""
    src = "struct S;\nimpl S {\n    fn run() {\n        #[tokio::main]\n        async fn inner() { helper(); }\n        inner();\n    }\n}\nfn helper() {}\n"
    trusted = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO, "src/lib.rs": src})
    assert helper_verdict(trusted) == TRUSTED


def test_a_trusted_function_in_the_body_of_an_impl_method_is_refused_when_the_repo_does_not_prove_it(make_repo):
    src = "struct S;\nimpl S {\n    fn run() {\n        #[tokio::main]\n        async fn inner() { helper(); }\n        inner();\n    }\n}\nfn helper() {}\n"
    refused = graph_of(make_repo, {"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "t" }\n', "src/lib.rs": src})
    assert helper_verdict(refused) == UNTRUSTED


# ---- a refusal changes nothing a macro the rule does not know would not change


def swap(source, attribute, unknown):
    """The source with each `#[attribute` written as `#[unknown` (a macro of a crate the manifest does not list)."""
    return re.sub(r"#\[" + re.escape(attribute) + r"\b", "#[" + unknown, source)


def dump(graph, **names):
    """The graph as json text, with the written form of the attribute in a reason put back to one name."""
    text = json.dumps(graph.to_dict(), sort_keys=True)
    for written, normal in names.items():
        text = text.replace(written, normal)
    return text


EQUIVALENT = {
    # a refused tokio::main beside an attribute macro that is not on the list, written before it and after it
    "first": ("#[tokio::main]\nasync fn main() {\n    helper();\n    S::f();\n}\n#[other::trace]\nfn traced() {}\n", "tokio::main", "zzz::main"),
    "last": ("#[other::trace]\nfn traced() {}\n#[tokio::main]\nasync fn main() {\n    helper();\n    S::f();\n}\n", "tokio::main", "zzz::main"),
    "everything_a_body_can_hold": (
        "#[tokio::main]\nasync fn main() {\n    struct T0;\n    trait Tr {\n        fn d() { helper(); }\n        fn e();\n    }\n"
        "    impl Tr for T0 {\n        fn e() { helper(); }\n    }\n    impl T0 {\n        const C: u8 = 1;\n        fn f() { helper(); }\n    }\n"
        "    enum E { A }\n    #[tokio::test]\n    async fn inner() { helper(); }\n    fn nested() { helper(); T0::f(); }\n"
        "    T0::f();\n    <T0 as Tr>::e();\n    nested();\n    helper();\n    missing();\n}\n#[other::trace]\nfn traced() {}\n",
        "tokio::main",
        "zzz::main",
    ),
    "tokio_test": ("#[other::trace]\nfn traced() {}\n#[tokio::test]\nasync fn main() {\n    helper();\n    S::f();\n}\n", "tokio::test", "zzz::test"),
}
BODY = "struct S;\nimpl S {\n    fn f() {}\n}\nfn helper() {}\nfn run() { S::f(); helper(); missing(); }\n"


@pytest.mark.parametrize("name", EQUIVALENT)
def test_a_refused_site_is_read_exactly_as_one_under_an_attribute_macro_that_is_not_known(make_repo, tmp_path_factory, name):
    source, listed, unknown = EQUIVALENT[name]
    refused = '[dependencies]\ntokio = { path = "t" }\nother = "1"\n'
    real = graph_of(make_repo, {"Cargo.toml": PKG + refused, "src/main.rs": source + BODY})
    twin = tmp_path_factory.mktemp("twin")
    for rel, written in {"Cargo.toml": PKG + refused, "src/main.rs": source.replace(listed, unknown) + BODY}.items():
        (twin / rel).parent.mkdir(parents=True, exist_ok=True)
        (twin / rel).write_text(written)
    expected = build_graph(twin, RUST)
    assert real.unresolved, "the refused function must have its calls unresolved"
    assert dump(real, **{listed: "L"}) == dump(expected, **{unknown: "L"})


def test_a_refused_serial_site_is_read_exactly_as_one_under_an_attribute_macro_that_is_not_known(make_repo, tmp_path_factory):
    manifest = PKG + SERIAL_TEST + OTHER
    real_src = SERIAL.replace("use serial_test::serial;", "use other::serial;") + BODY
    twin_src = SERIAL.replace("use serial_test::serial;", "use other::zzz;").replace("#[serial]", "#[zzz]") + BODY
    real = graph_of(make_repo, {"Cargo.toml": manifest, "src/lib.rs": real_src})
    twin = tmp_path_factory.mktemp("twin")
    (twin / "src").mkdir()
    (twin / "Cargo.toml").write_text(manifest)
    (twin / "src/lib.rs").write_text(twin_src)
    expected = build_graph(twin, RUST)
    assert real.unresolved
    assert dump(real, **{"#[serial]": "#[L]"}) == dump(expected, **{"#[zzz]": "#[L]"})


# ---- a refusal can make a scope one a macro may have made names in, which refuses the sites beside it


@pytest.mark.parametrize("first", ["refused", "trusted"])
def test_a_site_refused_makes_the_scope_one_where_a_macro_may_make_a_name_so_a_site_beside_it_is_refused_too(make_repo, first):
    """Both are in one module. `#[serial]` imported from another crate is an attribute macro that may generate a module
    named `tokio`; whether it is the one beside `#[tokio::main]` is decided first, and `tokio` in that scope then
    is not proven. The order the sites are written in does not matter."""
    refused = "#[serial]\nfn t() {\n    helper();\n}\n"
    trusted = "#[tokio::main]\nasync fn main() {\n    helper();\n}\n"
    body = (refused + trusted) if first == "refused" else (trusted + refused)
    src = "use other::serial;\nfn helper() {}\n" + body
    g = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": src})
    assert not g.edges
    assert {u.reason for u in g.unresolved if u.text == "helper"} == {rs.ATTR_MACRO}
    assert len([u for u in g.unresolved if u.text == "helper"]) == 2


def test_a_refusal_that_makes_one_scope_uncertain_reaches_the_next_through_a_chain_of_globs(make_repo):
    """m0's site is refused, so a glob of m0 may bring a `tokio` that a macro made, so m1's site is refused, so a glob of m1
    may too, and m2's is. Each step needs the one before it, so the decision runs until nothing changes: a pass that
    stopped early (decide_trust runs twice, which is two passes) would leave the third site trusted."""
    src = (
        "mod m0 {\n    use other::serial;\n    #[serial]\n    pub fn t() { super::helper(); }\n}\n"
        "mod m1 {\n    use super::m0::*;\n    #[tokio::main]\n    pub async fn y() { super::helper(); }\n}\n"
        "mod m2 {\n    use super::m1::*;\n    #[tokio::main]\n    pub async fn z() { super::helper(); }\n}\n"
        "fn helper() {}\n"
    )
    g = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": src})
    assert not g.edges
    assert [(u.caller, u.reason) for u in g.unresolved if u.text.endswith("helper")] == [
        ("src/main:m0::t", rs.ATTR_MACRO),
        ("src/main:m1::y", rs.ATTR_MACRO),
        ("src/main:m2::z", rs.ATTR_MACRO),
    ]


def test_a_site_in_a_module_beside_the_refused_one_is_not_refused(make_repo):
    src = "mod a {\n    use other::serial;\n    #[serial]\n    fn t() {\n        super::helper();\n    }\n}\nmod b {\n    #[tokio::main]\n    async fn main() {\n        super::helper();\n    }\n}\nfn helper() {}\n"
    g = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO + OTHER, "src/main.rs": src})
    assert {(e.caller, e.callee) for e in g.edges} == {("src/main:b::main", "src/main:helper")}
    assert {u.caller for u in g.unresolved} == {"src/main:a::t"}


# ---- a file two crates read: each crate proves the macro for itself


def test_a_file_read_by_a_crate_that_proves_the_macro_and_one_that_does_not_is_the_shared_file_reason(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
        "a/Cargo.toml": PKG.replace('"app"', '"a"') + TOKIO,
        "a/src/lib.rs": '#[path = "../../shared.rs"]\nmod shared;\n',
        "b/Cargo.toml": PKG.replace('"app"', '"b"') + '[dependencies]\ntokio = { path = "../t" }\n',
        "b/src/lib.rs": '#[path = "../../shared.rs"]\nmod shared;\n',
        "shared.rs": "#[tokio::main]\npub async fn main() {\n    helper();\n}\nfn helper() {}\n",
    }
    g = graph_of(make_repo, files)
    assert helper_verdict(g) == [rs.SHARED_FILE]


def test_a_file_read_by_two_crates_that_both_prove_it_is_an_edge(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
        "a/Cargo.toml": PKG.replace('"app"', '"a"') + TOKIO,
        "a/src/lib.rs": '#[path = "../../shared.rs"]\nmod shared;\n',
        "b/Cargo.toml": PKG.replace('"app"', '"b"') + TOKIO,
        "b/src/lib.rs": '#[path = "../../shared.rs"]\nmod shared;\n',
        "shared.rs": "#[tokio::main]\npub async fn main() {\n    helper();\n}\nfn helper() {}\n",
    }
    assert helper_verdict(graph_of(make_repo, files)) == TRUSTED


# ---- the manifest reader


def test_each_package_of_a_workspace_is_proven_on_its_own_manifest(make_repo):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["good", "bad"]\n',
        "good/Cargo.toml": PKG.replace('"app"', '"good"') + TOKIO,
        "good/src/main.rs": MAIN,
        "bad/Cargo.toml": PKG.replace('"app"', '"bad"') + '[dependencies]\ntokio = { path = "../t" }\n',
        "bad/src/main.rs": MAIN,
    }
    g = graph_of(make_repo, files)
    assert {(e.caller, e.callee) for e in g.edges} == {("good/src/main:main", "good/src/main:helper")}
    assert {u.caller for u in g.unresolved} == {"bad/src/main:main"}


def test_the_verdict_a_package_gets_per_crate_is_one_reason_or_none(make_repo):
    from ripple import rs_modules

    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN})
    package = rs_modules.load_workspace(repo, [repo / "src/main.rs"]).packages[""]
    assert package.trust["tokio"] is None
    assert "does not depend on serial_test" in package.trust["serial_test"]
    refused = make_repo({"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "t" }\n', "src/main.rs": MAIN})
    assert "not a plain registry version" in rs_modules.load_workspace(refused, [refused / "src/main.rs"]).packages[""].trust["tokio"]


# ---- what refusing a site alone still says of the impls of the crate (review F6)

CONV = "trait Conv<T> {\n    fn f();\n}\nstruct S;\nimpl Conv<u8> for S {\n    fn f() {}\n}\nfn run() {\n    <S as Conv<u8>>::f();\n}\n"


def test_a_refused_site_alone_says_the_impls_of_the_crate_may_not_be_all_there_are(make_repo):
    """The attribute macro of the one refused site may generate an impl, as any attribute macro may: with the site trusted the call
    below is an edge, with it refused (and nothing else in the crate under a macro) the impl it picks is not known to be the only one."""
    trusted = graph_of(make_repo, {"Cargo.toml": PKG + TOKIO, "src/main.rs": "#[tokio::main]\nasync fn main() {}\n" + CONV})
    assert {(e.caller, e.callee) for e in trusted.edges} == {("src/main:run", "src/main:<S as Conv>::f")}
    refused = graph_of(make_repo, {"Cargo.toml": PKG + '[dependencies]\ntokio = { path = "t" }\n', "src/main.rs": "#[tokio::main]\nasync fn main() {}\n" + CONV})
    assert not [e for e in refused.edges if e.callee.endswith("::f")]
    reasons = [u.reason for u in refused.unresolved if u.caller == "src/main:run"]
    assert len(reasons) == 1 and reasons[0].startswith(rs.IMPLS_INCOMPLETE.split("{}")[0]), reasons


def test_a_package_whose_trust_cannot_be_worked_out_is_refused_whatever_the_reason(make_repo, monkeypatch):
    """_trust reads manifests of any shape, and a shape it did not foresee must refuse: an exception is no verdict of trust."""
    from ripple import rs_modules

    def broken(*args):
        raise KeyError("workspace")

    monkeypatch.setattr(rs_modules, "_scope_problem", broken)
    repo = make_repo({"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN})
    trust = rs_modules.load_workspace(repo, [repo / "src/main.rs"]).packages[""].trust
    assert all("could not be read for this (KeyError)" in why for why in trust.values()), trust
    assert helper_verdict(graph_of(make_repo, {"Cargo.toml": PKG + TOKIO, "src/main.rs": MAIN})) == UNTRUSTED
