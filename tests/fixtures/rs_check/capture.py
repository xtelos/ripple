"""Write the tiny Cargo crates in this directory and capture a real `cargo test` run from each.

Run by hand with a Rust toolchain installed (offline, the crates need nothing from a registry):

    CARGO_HOME=... RUSTUP_HOME=... python3 tests/fixtures/rs_check/capture.py [cargo [run-name ...]]

With run names, only those runs (and their crates) are made again and every other capture stays as it is.

The crates are written under crates/, one directory each. Every run is then made from the
directory in `cwd` with the exact argv in `argv` (the command ripple's Rust runner must build for
the same request), and captures/<name>.txt holds the output byte for byte, stdout and stderr in
the order they were written (that is how ripple reads it), captures/<name>.json the rest: argv, cwd,
the exit code, whether ripple's own limit killed it, the toolchain version, `cargo metadata
--no-deps` for the workspace (its paths with the directory of the run written as @ROOT@) and
`request`, the `tests` argument that asks for it (null: no arguments).
tests/test_check_rs_output.py judges the captures without cargo and tests/test_check_rs_plan.py
checks that ripple builds each argv from its request; tests/test_check_rs_live.py runs the toolchain.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRATES = HERE / "crates"
CAPTURES = HERE / "captures"
# Runs happen in a copy here, so no capture carries the path of whoever made it.
WORK = Path("/tmp/ripple-rs-check-capture")


def package(name: str, extra: str = "") -> str:
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n{extra}'


ADD = "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"


def tests(body: str) -> str:
    return f"\n#[cfg(test)]\nmod tests {{\n    use super::*;\n{body}}}\n"


ADDS = "    #[test]\n    fn adds() {\n        assert_eq!(add(1, 2), 3);\n    }\n"
ADDS_ZERO = "    #[test]\n    fn adds_zero() {\n        assert_eq!(add(0, 0), 0);\n    }\n"
BAD = "    #[test]\n    fn bad() {\n        assert_eq!(add(2, 2), 5);\n    }\n"
DOC = "/// Adds.\n///\n/// ```\n/// assert_eq!(pass::add(1, 2), 3);\n/// ```\n"


def package24(name: str) -> str:
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2024"\n'


def doc(*blocks: str) -> str:
    """Each fenced block as the doc comment of a function of its own (f0, f1, ...)."""
    text = ""
    for index, block in enumerate(blocks):
        text += "".join(f"/// {line}\n" for line in block.splitlines()) + f"pub fn f{index}() {{}}\n\n"
    return text


# the doc tests rustdoc can merge on edition 2024, and those it runs as a binary of their own (`compile_fail`, `standalone_crate`)
DOC24 = doc(
    "```\nassert_eq!(lib24::f(), 1);\n```",
    "```should_panic\npanic!(\"expected\");\n```",
    "```no_run\nloop {}\n```",
    "```compile_fail\nlet x: i32 = \"s\";\n```",
    "```ignore\nnot rust\n```",
    "```standalone_crate\nassert_eq!(lib24::f(), 1);\n```",
)


def member(name: str, body: str) -> dict[str, str]:
    return {f"{name}/Cargo.toml": package(name), f"{name}/src/lib.rs": ADD + tests(body)}


SOURCES: dict[str, dict[str, str]] = {
    "pass": {"Cargo.toml": package("pass"), "src/lib.rs": DOC + ADD + tests(ADDS + ADDS_ZERO)},
    "fail": {"Cargo.toml": package("fail"), "src/lib.rs": ADD + tests(ADDS + BAD)},
    "build_failure": {
        "Cargo.toml": package("build_failure"),
        "src/lib.rs": "pub fn add(a: i32, b: i32) -> i32 {\n    a + missing\n}\n" + tests(ADDS),
    },
    "test_build_failure": {
        "Cargo.toml": package("test_build_failure"),
        "src/lib.rs": ADD + tests('    #[test]\n    fn typo() {\n        let n: i32 = "one";\n        assert_eq!(n, 1);\n    }\n'),
    },
    "build_script_failure": {
        "Cargo.toml": package("build_script_failure"),
        "build.rs": 'fn main() {\n    panic!("no way to build this");\n}\n',
        "src/lib.rs": ADD + tests(ADDS),
    },
    "no_tests": {"Cargo.toml": package("no_tests"), "src/lib.rs": ADD},
    "gated": {
        "Cargo.toml": package("gated", '\n[features]\nslow = []\n'),
        "src/lib.rs": ADD
        + tests(ADDS + '    #[cfg(feature = "slow")]\n    #[test]\n    fn slow() {\n        assert_eq!(add(1, 1), 2);\n    }\n'),
    },
    "ignored": {
        "Cargo.toml": package("ignored"),
        "src/lib.rs": ADD
        + tests(
            ADDS
            + "    #[test]\n    #[ignore]\n    fn slow() {\n        assert_eq!(add(1, 1), 2);\n    }\n"
            + '    #[test]\n    #[ignore = "needs a database"]\n    fn slower() {\n        assert_eq!(add(1, 1), 2);\n    }\n'
        ),
    },
    "doctest_failure": {
        "Cargo.toml": package("doctest_failure"),
        "src/lib.rs": "/// Adds.\n///\n/// ```\n/// assert_eq!(doctest_failure::add(1, 2), 4);\n/// ```\n" + ADD + tests(ADDS),
    },
    "abort": {
        "Cargo.toml": package("abort"),
        "src/lib.rs": ADD
        + tests(ADDS + "    #[test]\n    fn dies() {\n        std::thread::sleep(std::time::Duration::from_millis(400));\n        std::process::abort();\n    }\n"),
    },
    "exit_early": {
        "Cargo.toml": package("exit_early"),
        "src/lib.rs": ADD
        + tests(ADDS + "    #[test]\n    fn quits() {\n        std::thread::sleep(std::time::Duration::from_millis(400));\n        std::process::exit(0);\n    }\n"),
    },
    "killed": {
        "Cargo.toml": package("killed"),
        "src/lib.rs": ADD
        + tests(
            ADDS
            + "    #[test]\n    fn killed() {\n        std::thread::sleep(std::time::Duration::from_millis(400));\n        let pid = std::process::id().to_string();\n"
            + '        std::process::Command::new("kill").args(["-9", &pid]).status().unwrap();\n'
            + "        std::thread::sleep(std::time::Duration::from_secs(5));\n    }\n"
        ),
    },
    "sleepy": {
        "Cargo.toml": package("sleepy"),
        "src/lib.rs": ADD
        + tests(
            ADDS + "    #[test]\n    fn sleeps() {\n        std::thread::sleep(std::time::Duration::from_secs(60));\n    }\n"
        ),
    },
    "own_harness_panic": {
        "Cargo.toml": package("own_harness_panic", '\n[[test]]\nname = "it"\nharness = false\n'),
        "src/lib.rs": ADD,
        "tests/it.rs": 'fn main() {\n    panic!("the setup of this test is broken");\n}\n',
    },
    "own_harness_ok": {
        "Cargo.toml": package("own_harness_ok", '\n[[test]]\nname = "it"\nharness = false\n'),
        "src/lib.rs": ADD,
        "tests/it.rs": 'fn main() {\n    println!("all my own checks passed");\n}\n',
    },
    "dupes": {
        "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\nresolver = "2"\n',
        **member("a", "    #[test]\n    fn it_works() {\n        assert_eq!(add(1, 2), 3);\n    }\n" + "    #[test]\n    fn only_a() {\n        assert_eq!(add(0, 1), 1);\n    }\n"),
        **member("b", "    #[test]\n    fn it_works() {\n        assert_eq!(add(1, 2), 4);\n    }\n"),
    },
    "mixed": {
        "Cargo.toml": package("mixed"),
        "src/lib.rs": "pub mod net;\npub mod util;\n\npub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"
        + tests(
            "    #[test]\n    fn t_lib() {\n        assert_eq!(add(1, 2), 3);\n    }\n"
            + "    #[test]\n    fn same() {\n        assert_eq!(add(1, 1), 2);\n    }\n"
        ),
        "src/main.rs": "fn main() {\n    println!(\"{}\", mixed::add(1, 2));\n}\n"
        + "\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn t_bin() {\n        assert_eq!(1 + 2, 3);\n    }\n"
        + "    #[test]\n    fn same() {\n        assert_eq!(1 + 1, 2);\n    }\n}\n",
        "src/util.rs": "pub fn double(n: i32) -> i32 {\n    n * 2\n}\n"
        + tests("    #[test]\n    fn t_util() {\n        assert_eq!(double(2), 4);\n    }\n"),
        "src/net/mod.rs": "pub fn port() -> u16 {\n    80\n}\n"
        + tests("    #[test]\n    fn t_net() {\n        assert_eq!(port(), 80);\n    }\n"),
        "tests/it.rs": "mod common;\n\n#[test]\nfn t_it() {\n    assert_eq!(mixed::add(1, 2), 3);\n}\n\n#[test]\nfn same() {\n    assert_eq!(mixed::add(1, 1), 2);\n}\n",
        "tests/other.rs": "mod common;\n\n#[test]\nfn t_other() {\n    assert_eq!(mixed::add(2, 2), 4);\n}\n",
        "tests/common/mod.rs": "#[test]\nfn t_common() {\n    assert_eq!(1, 1);\n}\n",
    },
    "hyphen": {
        "Cargo.toml": package("hy-phen"),
        "src/lib.rs": ADD,
        "src/bin/my-tool.rs": "fn main() {}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn t_tool() {\n        assert_eq!(1 + 1, 2);\n    }\n}\n",
    },
    "needs_feature": {
        "Cargo.toml": package(
            "needs_feature", '\n[features]\nextra = []\n\n[[test]]\nname = "needs"\nrequired-features = ["extra"]\n'
        ),
        "src/lib.rs": ADD + tests(ADDS),
        "tests/needs.rs": "#[test]\nfn needs_the_feature() {\n    assert_eq!(needs_feature::add(1, 1), 2);\n}\n",
    },
    "printer": {
        "Cargo.toml": package("printer"),
        "src/lib.rs": ADD
        + tests(ADDS + '    #[test]\n    fn chatty() {\n        println!("hello from a test");\n        print!("and no newline");\n    }\n'),
    },
    "nested": {
        "Cargo.toml": '[workspace]\nmembers = ["app"]\nexclude = ["fixture"]\nresolver = "2"\n',
        **member("app", ADDS),
        **member("fixture", ADDS_ZERO),
        # a package in the workspace's directory that is neither a member nor excluded: cargo refuses to read it
        "stray/Cargo.toml": package("stray"),
        "stray/src/lib.rs": ADD + tests(ADDS),
    },
    "empty_target": {
        "Cargo.toml": package("empty_target"),
        "src/lib.rs": ADD + tests(ADDS),
        "tests/empty.rs": "fn helper() {}\n",
    },
    # every decoration libtest puts between a test's name and its result, in the unit tests and in the doc tests
    "decorated": {
        "Cargo.toml": package("decorated"),
        "src/lib.rs": "/// plain\n///\n/// ```\n/// assert_eq!(1, 1);\n/// ```\n///\n/// ```compile_fail\n/// let x: i32 = \"s\";\n/// ```\n///\n"
        + "/// ```no_run\n/// loop {}\n/// ```\n///\n/// ```should_panic\n/// panic!(\"doc boom\");\n/// ```\n///\n/// ```ignore\n/// not rust\n/// ```\n///\n"
        + "/// ```should_panic\n/// assert_eq!(1, 1);\n/// ```\npub fn f() {}\n"
        + tests(
            "    #[test]\n    #[should_panic]\n    fn sp() {\n        panic!(\"boom\");\n    }\n"
            + "    #[test]\n    #[should_panic(expected = \"boom\")]\n    fn sp_msg() {\n        panic!(\"boom here\");\n    }\n"
            + "    #[test]\n    #[should_panic(expected = \"boom\")]\n    fn sp_wrong_msg() {\n        panic!(\"other\");\n    }\n"
            + "    #[test]\n    #[should_panic]\n    fn sp_no_panic() {}\n"
            + "    #[test]\n    #[ignore]\n    #[should_panic]\n    fn sp_ignored() {\n        panic!(\"x\");\n    }\n"
            + "    #[test]\n    #[ignore = \"needs a database\"]\n    fn ign_reason() {}\n"
            + "    #[test]\n    fn r#match() {}\n"
            + "    #[test]\n    fn plain() {}\n"
        ),
    },
    # a test a macro writes, in a file other than the macro's: no reading of the file finds it
    "macgen": {
        "Cargo.toml": package("macgen"),
        "src/lib.rs": "#[macro_use]\nmod gen {\n    macro_rules! case {\n        ($name:ident, $want:expr) => {\n            #[test]\n            fn $name() {\n"
        + "                assert_eq!(crate::f(), $want);\n            }\n        };\n    }\n}\npub mod sub;\npub mod util;\npub fn f() -> i32 {\n    1\n}\n",
        "src/util.rs": "#[cfg(test)]\nmod tests {\n    #[test]\n    fn plain() {\n        assert_eq!(crate::f(), 1);\n    }\n\n    case!(generated_fails, 2);\n}\n",
        "src/sub/mod.rs": "#[cfg(test)]\nmod tests {\n    #[test]\n    fn sub_plain() {\n        assert_eq!(crate::f(), 1);\n    }\n\n    case!(sub_generated_fails, 2);\n}\n",
    },
    # the same with an attribute macro, as `#[rstest]` and `#[test_case]` do: the function is attributed, but not by `test`
    "attrgen": {
        "Cargo.toml": '[workspace]\nmembers = ["macros", "attrgen"]\nresolver = "2"\n',
        "macros/Cargo.toml": package("attrgen_macros", '\n[lib]\nproc-macro = true\n'),
        "macros/src/lib.rs": "use proc_macro::TokenStream;\n\n/// Keeps the function and adds two tests that call it, the way a parameterised test macro does.\n#[proc_macro_attribute]\n"
        + "pub fn twice(_args: TokenStream, item: TokenStream) -> TokenStream {\n    let text = item.to_string();\n"
        + "    let name = text.split(\"fn \").nth(1).and_then(|rest| rest.split('(').next()).unwrap_or(\"x\").trim().to_string();\n"
        + "    let extra = format!(\"#[test] fn {name}_a() {{ {name}() }} #[test] fn {name}_b() {{ {name}() }}\");\n"
        + "    let mut out = item;\n    out.extend(extra.parse::<TokenStream>().unwrap());\n    out\n}\n",
        "attrgen/Cargo.toml": package("attrgen", '\n[dependencies]\nattrgen_macros = { path = "../macros" }\n'),
        "attrgen/src/lib.rs": "pub mod util;\npub fn f() -> i32 {\n    1\n}\n",
        "attrgen/src/util.rs": "#[cfg(test)]\nmod tests {\n    use attrgen_macros::twice;\n\n    #[test]\n    fn plain() {\n        assert_eq!(crate::f(), 1);\n    }\n\n"
        + "    #[twice]\n    fn fine() {\n        assert_eq!(crate::f(), 1);\n    }\n\n    #[twice]\n    fn broken() {\n        assert_eq!(crate::f(), 2);\n    }\n}\n",
    },
    # a helper file of tests/ that another integration test pulls in with `mod util;`
    "helper": {
        "Cargo.toml": package("helper"),
        "src/lib.rs": "pub fn f() -> i32 {\n    1\n}\n",
        "tests/it.rs": "mod util;\n\n#[test]\nfn it_works() {\n    assert_eq!(helper::f(), util::setup());\n}\n",
        "tests/util.rs": "#[allow(dead_code)]\npub fn setup() -> i32 {\n    1\n}\n",
    },
    # an example with a test: `test = false` by default, and `cargo test --example demo` runs it
    "examples_ok": {
        "Cargo.toml": package("examples_ok"),
        "src/lib.rs": "pub fn f() -> i32 {\n    1\n}\n",
        "examples/demo.rs": "fn main() {}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn ex_ok() {\n        assert_eq!(examples_ok::f(), 1);\n    }\n}\n",
    },
    # a binary that exits 0 before libtest starts (a constructor in .init_array), beside a test that would fail
    "pre": {
        "Cargo.toml": package("pre"),
        "src/lib.rs": "pub fn f() -> i32 {\n    1\n}\n" + tests("    #[test]\n    fn ok() {\n        assert_eq!(super::f(), 1);\n    }\n"),
        "tests/early.rs": 'extern "C" fn bail() {\n    std::process::exit(0);\n}\n\n#[used]\n#[link_section = ".init_array"]\nstatic BAIL: extern "C" fn() = bail;\n\n'
        + "#[test]\nfn would_fail() {\n    assert_eq!(pre::f(), 2);\n}\n",
    },
    # a test that writes libtest's lines to stdout itself and exits 0 while the other one would fail
    "forge": {
        "Cargo.toml": package("forge"),
        "src/lib.rs": "pub fn f() -> i32 {\n    1\n}\n",
        "tests/it.rs": "use std::io::Write;\n\n#[test]\nfn a() {\n    let mut out = std::io::stdout();\n"
        + '    out.write_all(b"test a ... ok\\ntest b ... ok\\n\\ntest result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\\n\\n").unwrap();\n'
        + "    out.flush().unwrap();\n    std::process::exit(0);\n}\n\n#[test]\nfn b() {\n    std::thread::sleep(std::time::Duration::from_millis(500));\n    assert_eq!(forge::f(), 2);\n}\n",
    },
    # a module that is declared with #[path]: which tests are in its file cannot be taken from the module path with certainty
    "pathmod": {
        "Cargo.toml": package("pathmod"),
        "src/lib.rs": '#[path = "elsewhere/x.rs"]\nmod renamed;\npub fn f() -> i32 {\n    1\n}\n',
        "src/elsewhere/x.rs": "#[cfg(test)]\nmod tests {\n    #[test]\n    fn in_x() {\n        assert_eq!(crate::f(), 1);\n    }\n}\n",
    },
    "missing_dependency": {
        "Cargo.toml": package("missing_dependency", '\n[dependencies]\nripple_no_such_crate_xyz = "1"\n'),
        "src/lib.rs": ADD + tests(ADDS),
    },
    # edition 2024: rustdoc merges the doc tests it can into one binary and runs the rest (compile_fail, standalone_crate, an
    # edition override) as a SECOND libtest run, inside the same `Doc-tests` section of cargo's output
    "ed24": {
        "Cargo.toml": '[workspace]\nmembers = ["lib24", "other"]\nresolver = "2"\n',
        "lib24/Cargo.toml": package24("lib24"),
        "lib24/src/lib.rs": DOC24 + "pub fn f() -> i32 {\n    1\n}\n" + tests("    #[test]\n    fn unit() {\n        assert_eq!(f(), 1);\n    }\n"),
        **member("other", ADDS),
    },
    # the standalone doc test (the second run) fails, the merged one passes
    "ed24_fail_standalone": {
        "Cargo.toml": package24("ed24_fail_standalone"),
        "src/lib.rs": doc("```\nassert_eq!(1, 1);\n```", "```compile_fail\nlet x: i32 = \"s\";\n```", "```standalone_crate\nassert_eq!(1, 2);\n```") + "pub fn f() {}\n",
    },
    # the merged doc test (the first run) fails, the standalone one passes
    "ed24_fail_merged": {
        "Cargo.toml": package24("ed24_fail_merged"),
        "src/lib.rs": doc("```\nassert_eq!(1, 2);\n```", "```compile_fail\nlet x: i32 = \"s\";\n```", "```standalone_crate\nassert_eq!(1, 1);\n```") + "pub fn f() {}\n",
    },
    # every test in the file is #[ignore]: nothing in it ran, and one of them would fail
    "allig": {
        "Cargo.toml": package("allig"),
        "src/lib.rs": "pub mod util;\n\npub fn f() -> i32 {\n    1\n}\n" + tests("    #[test]\n    fn fine() {\n        assert_eq!(f(), 1);\n    }\n"),
        "src/util.rs": "#[cfg(test)]\nmod tests {\n    #[test]\n    #[ignore]\n    fn slow() {\n        assert_eq!(crate::f(), 1);\n    }\n\n"
        + '    #[test]\n    #[ignore = "broken"]\n    fn slower() {\n        assert_eq!(crate::f(), 2);\n    }\n}\n',
    },
    # modules a macro writes, with a failing test in each: one from a macro_rules in the same file (`here`), one from a macro defined in
    # ANOTHER file and invoked in a file of its own (`genmod::made`). No reading of the source finds either module.
    "genmod": {
        "Cargo.toml": package("genmod"),
        "src/lib.rs": "#[macro_use]\nmod macros;\n\nmacro_rules! mkmod_here {\n    ($n:ident) => {\n        mod $n {\n            #[test]\n            fn g() {\n"
        + "                assert_eq!(1, 2);\n            }\n\n            #[test]\n            fn ok() {}\n        }\n    };\n}\n\nmkmod_here!(here);\n\nmod genmod;\nmod nomacro;\n",
        "src/macros.rs": "macro_rules! mkmod {\n    ($n:ident) => {\n        mod $n {\n            #[test]\n            fn g() {\n                assert_eq!(1, 2);\n            }\n\n"
        + "            #[test]\n            fn ok() {}\n        }\n    };\n}\n",
        "src/genmod.rs": "mkmod!(made);\n",
        "src/nomacro.rs": "#[cfg(test)]\nmod tests {\n    #[test]\n    fn fine() {}\n}\n",
    },
    # raw identifiers in a module (a file and an inline one) and in a test's name, all failing
    "rawmod": {
        "Cargo.toml": package("rawmod"),
        "src/lib.rs": "pub mod r#type;\n\npub mod r#in {\n    #[cfg(test)]\n    mod tests {\n        #[test]\n        fn r#fn() {\n            assert_eq!(1, 2);\n        }\n    }\n}\n",
        "src/type.rs": "#[cfg(test)]\nmod tests {\n    #[test]\n    fn t_fails() {\n        assert_eq!(1, 2);\n    }\n\n    #[test]\n    fn r#match() {\n        assert_eq!(1, 2);\n    }\n}\n",
    },
}

CARGO_TEST = [
    "cargo", "test", "--color", "never", "--config", "term.quiet=false", "--config", "term.verbose=false",
    "--message-format", "json", "--no-fail-fast",
]


def spec(name, crate, argv, request, cwd=".", by_hand=False, timeout=600, env=None):
    """by_hand: the run asks for something ripple never builds (a filter that is a substring, a thread count), so no
    request maps to it and test_check_rs_plan.py leaves it out. timeout: the limit ripple's own timeout puts on the run."""
    return {"name": name, "crate": crate, "cwd": cwd, "argv": CARGO_TEST + argv, "request": request, "by_hand": by_hand, "timeout": timeout, "env": env or {}}


def exact(*names):
    return ["--", "--exact", *names]


RUNS = [
    spec("pass", "pass", ["--workspace"], None),
    spec("fail", "fail", ["--workspace"], None),
    spec("build_failure", "build_failure", ["--workspace"], None),
    spec("test_build_failure", "test_build_failure", ["--workspace"], None),
    spec("build_script_failure", "build_script_failure", ["--workspace"], None),
    spec("no_tests", "no_tests", ["--workspace"], None),
    spec("gated_slow", "gated", ["-p", "gated", "--lib"] + exact("tests::slow"), ["src/lib:tests::slow"]),
    spec("gated_adds", "gated", ["-p", "gated", "--lib"] + exact("tests::adds"), ["src/lib:tests::adds"]),
    spec("ignored_all", "ignored", ["--workspace"], None),
    spec("ignored_asked", "ignored", ["-p", "ignored", "--lib"] + exact("tests::slow"), ["src/lib:tests::slow"]),
    spec("doctest_failure", "doctest_failure", ["--workspace"], None),
    spec("abort", "abort", ["--workspace"], None),
    spec("exit_early", "exit_early", ["--workspace"], None),
    spec("killed", "killed", ["--workspace"], None),
    spec("timeout", "sleepy", ["--workspace"], None, timeout=12),
    spec("own_harness_panic", "own_harness_panic", ["--workspace"], None),
    spec("own_harness_ok", "own_harness_ok", ["--workspace"], None),
    spec("pass_adds", "pass", ["-p", "pass", "--lib"] + exact("tests::adds"), ["src/lib:tests::adds"]),
    spec("pass_substring", "pass", ["-p", "pass", "--lib", "--", "tests::adds"], None, by_hand=True),
    spec("pass_both", "pass", ["-p", "pass", "--lib"], ["src/lib.rs"]),  # a crate root file is the whole target
    spec("pass_nope", "pass", ["-p", "pass", "--lib"] + exact("tests::nope"), None, by_hand=True),
    spec("dupes_all", "dupes", ["--workspace"], None),
    spec("dupes_a", "dupes", ["-p", "a", "--lib"] + exact("tests::it_works"), ["a/src/lib:tests::it_works"]),
    spec("dupes_b", "dupes", ["-p", "b", "--lib"] + exact("tests::it_works"), ["b/src/lib:tests::it_works"]),
    spec("dupes_substring", "dupes", ["--workspace", "--", "it_works"], None, by_hand=True),
    spec("mixed_all", "mixed", ["--workspace"], None),
    spec("mixed_lib", "mixed", ["-p", "mixed", "--lib"] + exact("tests::same"), ["src/lib:tests::same"]),
    spec("mixed_bin", "mixed", ["-p", "mixed", "--bin", "mixed"] + exact("tests::same"), ["src/main:tests::same"]),
    spec("mixed_it", "mixed", ["-p", "mixed", "--test", "it"] + exact("same"), ["tests/it:same"]),
    spec("mixed_it_file", "mixed", ["-p", "mixed", "--test", "it"], ["tests/it.rs"]),
    spec("mixed_util_file", "mixed", ["-p", "mixed", "--lib"] + exact("util::tests::t_util"), ["src/util.rs"]),
    spec("mixed_net_dir", "mixed", ["-p", "mixed", "--lib"] + exact("net::tests::t_net"), ["src/net"]),
    # a file two test targets both include is two runs, one per target (a name filter is shared by every binary of a run)
    spec("mixed_common_it", "mixed", ["-p", "mixed", "--test", "it"] + exact("common::t_common"), ["tests/common/mod.rs"]),
    spec("mixed_common_other", "mixed", ["-p", "mixed", "--test", "other"] + exact("common::t_common"), ["tests/common/mod.rs"]),
    spec("hyphen_bin", "hyphen", ["-p", "hy-phen", "--bin", "my-tool"] + exact("tests::t_tool"), ["src/bin/my-tool:tests::t_tool"]),
    spec("needs_feature_all", "needs_feature", ["--workspace"], None),
    spec("needs_feature_asked", "needs_feature", ["-p", "needs_feature", "--test", "needs"], ["tests/needs.rs"], by_hand=True),  # ripple refuses this one: the capture is what cargo says if it were asked
    spec("missing_dependency", "missing_dependency", ["--workspace"], None),
    spec("empty_target", "empty_target", ["--workspace"], None),
    spec("decorated", "decorated", ["--workspace"], None),
    spec("decorated_sp", "decorated", ["-p", "decorated", "--lib"] + exact("tests::sp"), ["src/lib:tests::sp"]),
    spec("decorated_sp_msg", "decorated", ["-p", "decorated", "--lib"] + exact("tests::sp_msg"), ["src/lib.rs::tests::sp_msg"]),
    spec("decorated_sp_no_panic", "decorated", ["-p", "decorated", "--lib"] + exact("tests::sp_no_panic"), ["src/lib:tests::sp_no_panic"]),
    spec("decorated_raw", "decorated", ["-p", "decorated", "--lib"] + exact("tests::r#match"), ["src/lib.rs::tests::r#match"]),
    spec("decorated_lib_root", "decorated", ["-p", "decorated", "--lib"], ["src/lib.rs"]),
    spec("macgen_all", "macgen", ["--workspace"], None),
    spec("macgen_file", "macgen", ["-p", "macgen", "--lib"] + exact("util::tests::generated_fails", "util::tests::plain"), ["src/util.rs"]),
    spec("macgen_dir", "macgen", ["-p", "macgen", "--lib"] + exact("sub::tests::sub_generated_fails", "sub::tests::sub_plain"), ["src/sub"]),
    spec("macgen_src_dir", "macgen", ["-p", "macgen", "--lib"], ["src"]),
    spec("macgen_id", "macgen", ["-p", "macgen", "--lib"] + exact("util::tests::generated_fails"), ["src/util.rs::tests::generated_fails"]),
    spec("macgen_missing_id", "macgen", ["-p", "macgen", "--lib"] + exact("util::tests::not_a_test"), ["src/util.rs::tests::not_a_test"]),
    spec("attrgen_all", "attrgen", ["--workspace"], None),
    spec(
        "attrgen_file", "attrgen",
        ["-p", "attrgen", "--lib"] + exact("util::tests::broken_a", "util::tests::broken_b", "util::tests::fine_a", "util::tests::fine_b", "util::tests::plain"),
        ["attrgen/src/util.rs"],
    ),
    spec("helper_all", "helper", ["--workspace"], None),
    spec("example_file", "examples_ok", ["-p", "examples_ok", "--example", "demo"], ["examples/demo.rs"]),
    spec("example_id", "examples_ok", ["-p", "examples_ok", "--example", "demo"] + exact("tests::ex_ok"), ["examples/demo:tests::ex_ok"]),
    spec("pre_all", "pre", ["--workspace"], None),
    spec("pre_early", "pre", ["-p", "pre", "--test", "early"] + exact("would_fail"), ["tests/early:would_fail"]),
    spec("forge_all", "forge", ["--workspace"], None),
    spec("forge_a", "forge", ["-p", "forge", "--test", "it"] + exact("a"), ["tests/it:a"]),
    spec("pathmod_file", "pathmod", ["-p", "pathmod", "--lib"], ["src/elsewhere/x.rs"]),
    # environment that changes how cargo or libtest prints: the argv makes cargo ignore the first three, and libtest's is a problem, not a pass
    spec(
        "pass_noisy_env", "pass", ["--workspace"], None,
        env={"CARGO_TERM_QUIET": "true", "CARGO_TERM_VERBOSE": "true", "CARGO_TERM_COLOR": "always"},
    ),
    spec("printer_nocapture", "printer", ["--workspace"], None, env={"RUST_TEST_NOCAPTURE": "1"}),
    spec("printer", "printer", ["--workspace"], None),
    spec("ed24_all", "ed24", ["--workspace"], None),
    spec("ed24_pkg", "ed24", ["-p", "lib24"], ["lib24"]),
    spec("ed24_fail_standalone", "ed24_fail_standalone", ["--workspace"], None),
    spec("ed24_fail_merged", "ed24_fail_merged", ["--workspace"], None),
    spec("allig_file", "allig", ["-p", "allig", "--lib"] + exact("util::tests::slow", "util::tests::slower"), ["src/util.rs"]),
    spec("genmod_all", "genmod", ["--workspace"], None),
    spec("genmod_made_id", "genmod", ["-p", "genmod", "--lib"] + exact("genmod::made::g"), ["src/genmod.rs::made::g"]),
    spec("genmod_here_id", "genmod", ["-p", "genmod", "--lib"] + exact("here::g"), ["src/lib.rs::here::g"]),
    spec("genmod_file", "genmod", ["-p", "genmod", "--lib"] + exact("genmod::made::g", "genmod::made::ok"), ["src/genmod.rs"]),
    # a typo in the module of a file with macros: not refused up front (a macro may write it), the list has no such test
    spec("genmod_typo", "genmod", ["-p", "genmod", "--lib"] + exact("genmod::made_typo::g"), ["src/genmod.rs::made_typo::g"]),
    spec("rawmod_all", "rawmod", ["--workspace"], None),
    spec("rawmod_file", "rawmod", ["-p", "rawmod", "--lib"] + exact("r#type::tests::r#match", "r#type::tests::t_fails"), ["src/type.rs"]),
    spec("forge_it_file", "forge", ["-p", "forge", "--test", "it"], ["tests/it.rs"]),  # a file that is a whole target has no list to catch a forgery with
    spec("nested_all", "nested", ["--workspace"], None),
    spec("nested_fixture", "nested", ["--workspace"], ["fixture"], cwd="fixture"),
]


# When does rustdoc run doc tests as more than one libtest run? One crate per case, each with the doc tests of the case on `f0`, `f1`, ...
# (measured on cargo 1.99.1: tests/test_check_rs_sections.py holds the runs each capture has). It does only on edition 2024, and only
# when it has something to merge and something it cannot: `compile_fail`, `standalone_crate` and an `editionNNNN` attribute are run
# apart, and so is an `ignore` snippet that is not Rust syntax (`not rust`; `nope` parses, so it is merged); `no_run`, `should_panic` and
# crate attributes are merged.
PLAIN, OTHER = "```\nassert_eq!(1, 1);\n```", "```\nassert_eq!(2, 2);\n```"
DOC_SPLIT = {
    "plain_plain": [PLAIN, OTHER],
    "compile_fail": [PLAIN, "```compile_fail\nlet x: i32 = \"s\";\n```"],
    "no_run": [PLAIN, "```no_run\nloop {}\n```"],
    "ignore": [PLAIN, "```ignore\nnope\n```"],
    "ignore_not_rust": [PLAIN, "```ignore\nnot rust\n```"],
    "should_panic": [PLAIN, "```should_panic\npanic!(\"x\");\n```"],
    "standalone": [PLAIN, "```standalone_crate\nassert_eq!(1, 1);\n```"],
    "edition2015": [PLAIN, "```edition2015\nassert_eq!(1, 1);\n```"],
    "edition2018": [PLAIN, "```edition2018\nassert_eq!(1, 1);\n```"],
    "edition2021": [PLAIN, "```edition2021\nassert_eq!(1, 1);\n```"],
    "crate_attr": [PLAIN, "```\n#![allow(unused)]\nassert_eq!(1, 1);\n```"],
    "compile_fail_only": ["```compile_fail\nlet x: i32 = \"s\";\n```"],
    "standalone_only": ["```standalone_crate\nassert_eq!(1, 1);\n```"],
}
for _case, _blocks in DOC_SPLIT.items():
    SOURCES[f"docsplit24_{_case}"] = {"Cargo.toml": package24(f"docsplit24_{_case}"), "src/lib.rs": doc(*_blocks)}
    RUNS.append(spec(f"docsplit24_{_case}", f"docsplit24_{_case}", ["--workspace"], None))
for _case in ("compile_fail", "standalone", "edition2015"):  # the same on edition 2021: one run
    SOURCES[f"docsplit21_{_case}"] = {"Cargo.toml": package(f"docsplit21_{_case}"), "src/lib.rs": doc(*DOC_SPLIT[_case])}
    RUNS.append(spec(f"docsplit21_{_case}", f"docsplit21_{_case}", ["--workspace"], None))


def write_crates(only: set[str] | None = None) -> None:
    """Every crate (the directory is made anew), or with `only` the crates of those runs, written over what is there."""
    if only is None:
        shutil.rmtree(CRATES, ignore_errors=True)
    for name, files in SOURCES.items():
        if only is not None and name not in only:
            continue
        if only is not None:
            shutil.rmtree(CRATES / name, ignore_errors=True)
        for rel, text in files.items():
            path = CRATES / name / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)


def env_for(target: Path) -> dict:
    env = dict(os.environ)
    env.update({"CARGO_NET_OFFLINE": "true", "CARGO_TARGET_DIR": str(target), "CARGO_TERM_COLOR": "never"})
    env.pop("RUST_BACKTRACE", None)
    return env


def capture(cargo: str, only: set[str] | None = None) -> None:
    """Every run, or with `only` just the runs of those names (the other captures are left as they are)."""
    sys.path.insert(0, str(HERE.parents[2]))
    from ripple.checker import run_process

    if only is None:
        shutil.rmtree(CAPTURES, ignore_errors=True)
    CAPTURES.mkdir(exist_ok=True)
    for item in RUNS:
        if only is not None and item["name"] not in only:
            continue
        root = WORK / item["crate"]
        shutil.rmtree(root, ignore_errors=True)
        shutil.copytree(CRATES / item["crate"], root)
        cwd = (root / item["cwd"]).resolve()
        target = WORK / f"target-{item['name']}"
        shutil.rmtree(target, ignore_errors=True)
        for key in ("CARGO_TERM_QUIET", "CARGO_TERM_VERBOSE", "RUST_TEST_NOCAPTURE"):
            os.environ.pop(key, None)
        os.environ.update(env_for(target))
        os.environ.update(item["env"])
        argv = [cargo] + item["argv"][1:]
        # the run is made the way ripple makes it: output in the order it was written, killed when the limit is used up
        # a request that is not a whole target is first asked what the test binary lists (`-- --list`), as ripple asks it
        listing = None
        if item["request"] and "--" in item["argv"] and not item["by_hand"]:
            list_argv = [cargo] + item["argv"][1 : item["argv"].index("--")] + ["--", "--list"]
            listing = run_process(list_argv, cwd, item["timeout"])
            (CAPTURES / f"{item['name']}.list.txt").write_text(listing.stdout.replace(str(root), "@ROOT@").replace(str(target), "@TARGET@"))
        outcome = run_process(argv, cwd, item["timeout"])
        meta_run = subprocess.run(
            [cargo, "metadata", "--no-deps", "--format-version", "1"], cwd=cwd, capture_output=True, text=True, timeout=120
        )
        (CAPTURES / f"{item['name']}.txt").write_text(outcome.stdout.replace(str(root), "@ROOT@").replace(str(target), "@TARGET@"))
        meta = {
            "crate": item["crate"],
            "cwd": item["cwd"],
            "argv": item["argv"],
            "request": item["request"],
            "by_hand": item["by_hand"],
            "exit_code": None if outcome.timed_out else outcome.returncode,
            "timed_out": outcome.timed_out,
            "timeout": item["timeout"],
            "env": item["env"],
            "listed": listing is not None,
            "cargo_version": subprocess.run([cargo, "--version"], capture_output=True, text=True).stdout.strip(),
            "metadata": meta_run.stdout.replace(str(root), "@ROOT@") if meta_run.returncode == 0 else None,
        }
        (CAPTURES / f"{item['name']}.json").write_text(json.dumps(meta, indent=2) + "\n")
        shutil.rmtree(target, ignore_errors=True)
        print(item["name"], meta["exit_code"], len(outcome.stdout.splitlines()), "lines")


if __name__ == "__main__":
    wanted = set(sys.argv[2:]) or None
    write_crates({item["crate"] for item in RUNS if item["name"] in wanted} if wanted else None)
    capture(sys.argv[1] if len(sys.argv) > 1 else "cargo", wanted)
