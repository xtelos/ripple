"""Write the oracle of the Rust probe crates: what rust-analyzer says each call site calls.

    python -m bench.rs_probe_oracle [--toolchain DIR] [--out DIR] [--only NAME ...]

Run by hand, like a fixture generator: it needs a Rust toolchain with rust-analyzer (cargo, rustc and rust-analyzer
under DIR/cargo/bin, as rustup lays them out; default /tmp/rust-toolchain, or $RIPPLE_RUST_TOOLCHAIN), and writes
only to a temporary directory and to the oracle files. The tests (tests/test_rs_probes.py) read those files and
need no Rust toolchain.

For every crate in bench/fixtures/rust-probes/crates (a directory beginning with `_` is a dependency outside the
probes, not a probe) it
  1. copies the crate to a temporary directory and runs `cargo check --all-targets --workspace` there: a probe that
     does not compile is a mistake of the probe, and stops the run;
  2. runs `rust-analyzer scip` on the copy and reads the index (a protobuf, decoded here without a library);
  3. keeps every reference that is written as a call (its name is followed by `(` or `::<`), with the repo
     definition rust-analyzer resolved it to (file and line), or "external" (it resolved outside the repo) or
     "local" (a value bound in the body: a closure, a parameter).
A probe in RUNTIME is one rust-analyzer cannot answer (derive-generated code): its answers are written below, each
proven by the assertions of the crate's own `#[test]`s, which `cargo test` runs here instead.
A crate with `#[test]`s has them run for every probe (a probe whose tests fail stops the run), and where rust-analyzer's
answer is not what runs (it does not expand a procedural macro, and it names a function a macro generates by a symbol
with no definition) PROVEN gives the answer the tests prove.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

PROBES = Path(__file__).resolve().parent / "fixtures" / "rust-probes"
CRATES = PROBES / "crates"
ORACLE = PROBES / "oracle"
CALLED = re.compile(r"\s*(\(|::\s*<)")

# probe -> [(line, name text, "external" or "repo:<file>:<line>")] that rust-analyzer cannot answer at all, answered by
# the crate's own assertions
RUNTIME: dict[str, list[tuple[int, str, str]]] = {
    # `D::default()` and `D::clone(&a)` are what #[derive(Default, Clone)] generates: the crate's assertions show they
    # are not `Maker::default` and `Maker::clone` (5 and 6), whose impl is for another type
    "derive_runtime": [
        (16, "default", "external"),
        (17, "clone", "external"),
        (18, "default", "external"),
        (19, "default", "repo:src/lib.rs:12"),
        (19, "clone", "repo:src/lib.rs:13"),
    ],
}

# probe -> {(file, line, name, n): answer} where the answer rust-analyzer gives is not what runs, proven by the assertions
# of the crate's own `#[test]` (which are run before): the answer is "repo:<file>:<line>", "external" or "lost" (a function
# a macro generates, which no file defines). It replaces rust-analyzer's, whatever that was.
PROVEN: dict[str, dict[tuple[str, int, str, int], str]] = {
    # a function an attribute macro appends to a block of a function: rust-analyzer finds the module's `helper`, and the
    # assertions show the generated one ran (2, not 100)
    "r2_q8_attr_proc_macro": {("app/src/lib.rs", 13, "helper", 0): "lost"},
    # an impl a macro generates inside a function body applies to the whole crate: rust-analyzer does not see it from outside
    # the body, and the assertion shows the generated inherent function ran (2, not the trait impl's 1)
    "r3_macro_in_fn_body_impl": {("src/lib.rs", 9, "f", 0): "lost"},
    "r3_attr_macros": {
        ("app/src/lib.rs", 12, "helper", 0): "lost",
        # `#[pm::swap_one_for_two]` rewrites `one()` to `two()`: rust-analyzer resolves the call as written
        ("app/src/lib.rs", 18, "one", 0): "repo:app/src/lib.rs:16",
    },
    # The trust_* probes below are attribute macros ripple would trust if the attribute were the published tokio's or
    # serial_test's (rs_indexer.TRUSTED_ATTRS). In each, the attribute is a macro of crates/_ext/ that rewrites `one()` into
    # `two()` while rust-analyzer resolves the call as written: the crate's own #[test] shows `two` ran, so a ripple edge to
    # `one` would be a false one and these answers make the differential suite say so. The probes that are not here
    # (trust_tokio_main and the other positives) run the published crates, whose body is what it says, and the oracle is
    # right as written.
    "trust_path_dep_named_tokio": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_renamed_dep": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_workspace_path": {("member/src/lib.rs", 5, "one", 0): "repo:member/src/lib.rs:3"},
    "trust_target_table_path": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_patch_macro_crate": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_config_paths": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_replace": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_extern_crate_alias": {("src/lib.rs", 6, "one", 0): "repo:src/lib.rs:4"},
    "trust_repo_is_tokio": {("tests/own_main.rs", 5, "one", 0): "repo:tests/own_main.rs:3"},
    "trust_shadowed_names": {
        ("src/lib.rs", 13, "one", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 18, "one", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 23, "one", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 28, "one", 0): "repo:src/lib.rs:3",
    },
    # the review's probes, each a way to be another macro that the first rules missed: a `use` of a macro called `test` beside a
    # trusted attribute (#[test] is the prelude's macro, which a use replaces), a [replace] key written `name@version` or as a url,
    # a workspace root manifest that cargo reads and a TOML 1.0 parser does not, a workspace root that package.workspace names
    # and no source sits under, and a `mod tokio` written in a block
    "trust_test_shadowed": {("src/lib.rs", 7, "one", 0): "repo:src/lib.rs:4"},
    "trust_replace_at": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_replace_url": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_root_manifest_toml_1_1": {("app/src/lib.rs", 5, "one", 0): "repo:app/src/lib.rs:3"},
    "trust_workspace_named_outside": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_block_mod_tokio": {("src/lib.rs", 9, "one", 0): "repo:src/lib.rs:3"},
    # the second review's probes: a tool namespace (`rustfmt`, `clippy`, `diagnostic`) or another name of the compiler's attribute
    # list that a `mod`, a `use`, an `extern crate` or a dependency key makes another macro; a cargo config that is read through a
    # symlinked `.cargo`, or from a package in a directory called `target`; what a block `mod` holds acting outside it; an outside
    # glob that a block `mod` of the same name must not make local; a `test` in a `cfg_attr` on a method
    "trust_tool_shadow": {
        ("src/lib.rs", 14, "one", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 20, "one", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 25, "one", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 31, "one", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 39, "one", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 49, "one", 0): "repo:src/lib.rs:3",
    },
    "trust_tool_dep_key": {("src/lib.rs", 6, "one", 0): "repo:src/lib.rs:3", ("src/lib.rs", 8, "one", 0): "repo:src/lib.rs:3"},
    "trust_symlinked_cargo_config": {("src/lib.rs", 5, "one", 0): "repo:src/lib.rs:3"},
    "trust_target_dir_config": {("crates/target/src/lib.rs", 5, "one", 0): "repo:crates/target/src/lib.rs:3"},
    "trust_cfgattr_method": {("src/lib.rs", 8, "one", 0): "repo:src/lib.rs:4"},
    # rust-analyzer reads the impl of a block mod as it reads the trait's: `S::new()` runs the inherent `new` (the assertion shows 2)
    "trust_block_mod_effects": {("src/lib.rs", 30, "new", 0): "repo:src/lib.rs:27"},
    "trust_outside_glob_block_mod": {("src/lib.rs", 13, "helper", 0): "lost"},
    # a derive emits `fn one()` beside the type, which beats the glob's `one` (rust-analyzer does not expand it): the function is one no
    # file defines. The derive is not the standard library's, or has the name of one that a `use` replaced
    "trust_derive_unknown": {("src/lib.rs", 11, "one", 0): "lost"},
    "trust_derive_shadow": {("src/lib.rs", 12, "one", 0): "lost"},
    # `mod std` re-exporting the other derive makes `use std::fmt::Debug;` that derive (built: the crate's test asserts 2), so a use of
    # the spelling is not the standard library's unless `std` is proved to be the crate. `trust_derive_std_use` is the control: the real
    # `std::fmt::Debug` emits nothing, and the glob's `one` is the call's callee (no proof needed: rust-analyzer says the same)
    "trust_derive_std_shadow": {("src/lib.rs", 17, "one", 0): "lost"},
}

# probe -> the directory of the probe where its package is, and where cargo runs: a probe whose point is a file cargo reads from the
# directory it is run in (a `.cargo/config.toml` of a package that sits in a directory called `target`). The oracle's files and the
# answers above are still named from the probe's own root.
CARGO_DIR: dict[str, str] = {"trust_target_dir_config": "crates/target"}

# rust-analyzer names a function by its path and not by its block, so a function declared in a body has the same symbol
# as a module-level one of its name (and a library and a binary of one package share symbols, and so do the test
# crates of one package). Where that makes its answer a list of definitions, the one that runs is written here,
# each proven by a `#[test]` of the crate (run by `cargo test` below): probe -> {(file, line, name, n): answer}, the
# nth reference of that name on that line, left to right. The answer is "repo:<file>:<line>", "shared" (the file is
# read as a module of several crates, which resolve it differently) or "foreign" (a function an extern block declares).
OVERRIDES: dict[str, dict[tuple[str, int, str, int], str]] = {
    "block_scopes": {
        ("src/lib.rs", 13, "f", 0): "repo:src/lib.rs:12",
        ("src/lib.rs", 22, "f", 0): "repo:src/lib.rs:6",
        ("src/lib.rs", 23, "f", 0): "repo:src/lib.rs:23",
        ("src/lib.rs", 38, "f", 0): "repo:src/lib.rs:6",
    },
    "impl_in_fn_body": {
        ("src/lib.rs", 10, "new", 0): "repo:src/lib.rs:9",
        ("src/lib.rs", 12, "new", 0): "repo:src/lib.rs:9",
        ("src/lib.rs", 14, "new", 0): "repo:src/lib.rs:4",
    },
    "macro_defines": {
        ("src/lib.rs", 23, "plain", 0): "repo:src/lib.rs:21",
        ("src/lib.rs", 30, "plain", 0): "repo:src/lib.rs:29",
    },
    "foreign_fns": {
        ("src/lib.rs", 9, "abs", 0): "foreign",
        ("src/lib.rs", 9, "labs", 0): "foreign",
        ("src/lib.rs", 13, "abs", 0): "repo:src/lib.rs:12",
    },
    "std_macro_blocks": {
        ("src/lib.rs", 9, "g", 0): "repo:src/lib.rs:9",
        ("src/lib.rs", 10, "g", 0): "repo:src/lib.rs:6",
        ("src/lib.rs", 14, "g", 0): "repo:src/lib.rs:14",
        ("src/lib.rs", 15, "g", 0): "repo:src/lib.rs:15",
        ("src/lib.rs", 20, "g", 0): "repo:src/lib.rs:20",
        ("src/lib.rs", 22, "g", 0): "repo:src/lib.rs:6",
        ("src/lib.rs", 25, "g", 0): "repo:src/lib.rs:6",
        ("src/lib.rs", 25, "g", 1): "repo:src/lib.rs:6",
    },
    "lib_bin_same_name": {
        ("src/lib.rs", 4, "f", 0): "repo:src/lib.rs:2",
        ("src/main.rs", 4, "f", 0): "repo:src/lib.rs:2",
        ("src/main.rs", 4, "f", 1): "repo:src/main.rs:2",
        ("src/main.rs", 4, "f", 2): "repo:src/main.rs:2",
        ("src/main.rs", 10, "f", 0): "repo:src/main.rs:2",
        ("src/main.rs", 17, "f", 0): "repo:src/lib.rs:2",
        ("src/main.rs", 17, "f", 1): "repo:src/main.rs:2",
        ("src/main.rs", 17, "f", 2): "repo:src/main.rs:2",
        ("src/local.rs", 1, "f", 0): "repo:src/main.rs:2",
        ("src/local.rs", 1, "f", 1): "repo:src/lib.rs:2",
    },
    "r2_q3_private_const_cfg_raw": {
        ("src/lib.rs", 32, "helper", 0): "repo:src/lib.rs:32",
    },
    "r3_raw_identifiers": {
        ("src/lib.rs", 4, "helper", 0): "repo:src/lib.rs:4",
    },
    "file_in_two_crates": {
        ("src/lib.rs", 4, "local", 0): "repo:src/lib.rs:3",
        ("src/lib.rs", 6, "local", 0): "repo:src/lib.rs:3",
        ("src/main.rs", 4, "local", 0): "repo:src/main.rs:2",
        ("src/main.rs", 7, "local", 0): "repo:src/main.rs:2",
        ("src/shared.rs", 1, "local", 0): "shared",
    },
    "review_p2_workspace_twins": {
        ("app/src/lib.rs", 15, "g", 0): "repo:app/src/lib.rs:8",
        ("app/src/lib.rs", 32, "g", 0): "repo:app/src/lib.rs:32",
        ("app/src/main.rs", 2, "g", 0): "repo:app/src/lib.rs:8",
        ("app/src/main.rs", 5, "g", 0): "repo:app/src/lib.rs:8",
        ("app/tests/common/mod.rs", 3, "local_helper", 0): "shared",
    },
}


# ---- a minimal reader of a SCIP index: documents -> occurrences, and where each symbol is defined


def varint(b: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        c = b[i]
        i += 1
        result |= (c & 0x7F) << shift
        if not c & 0x80:
            return result, i
        shift += 7


def fields(b: bytes):
    i = 0
    while i < len(b):
        tag, i = varint(b, i)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = varint(b, i)
        elif wire == 2:
            n, i = varint(b, i)
            value = b[i : i + n]
            i += n
        elif wire == 1:
            value, i = b[i : i + 8], i + 8
        elif wire == 5:
            value, i = b[i : i + 4], i + 4
        else:
            raise ValueError(f"wire type {wire}")
        yield number, wire, value


def packed(b: bytes) -> list[int]:
    i, out = 0, []
    while i < len(b):
        value, i = varint(b, i)
        out.append(value)
    return out


def read_scip(path: Path):
    """({file: [(line, col0, col1, symbol, roles)]}, {symbol: [(file, line, col)]} for its definitions)."""
    docs: dict[str, list] = {}
    defs: dict[str, list] = defaultdict(list)
    for number, _, value in fields(path.read_bytes()):
        if number != 2:
            continue
        rel, occurrences = None, []
        for n2, w2, v2 in fields(value):
            if n2 == 1:
                rel = v2.decode()
            elif n2 == 2:
                span, symbol, roles = [], "", 0
                for n3, w3, v3 in fields(v2):
                    if n3 == 1:
                        span += packed(v3) if w3 == 2 else [v3]
                    elif n3 == 2:
                        symbol = v3.decode()
                    elif n3 == 3:
                        roles = v3
                if len(span) >= 3:
                    occurrences.append((span[0], span[1], span[2] if len(span) == 3 else span[3], symbol, roles))
        docs[rel] = occurrences
        for line, c0, _, symbol, roles in occurrences:
            if roles & 1:
                defs[symbol].append((rel, line, c0))
    return docs, defs


# ---- one probe


def environment(toolchain: Path) -> dict[str, str]:
    return {
        **os.environ,
        "RUSTUP_HOME": str(toolchain / "rustup"),
        "CARGO_HOME": str(toolchain / "cargo"),
        "PATH": f"{toolchain / 'cargo' / 'bin'}:{os.environ['PATH']}",
    }


def run(argv: list[str], cwd: Path, env: dict) -> str:
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=900, check=False)
    if done.returncode != 0:
        sys.exit(f"{' '.join(argv)} failed in {cwd}:\n{done.stdout[-2000:]}\n{done.stderr[-4000:]}")
    return done.stdout + done.stderr


def occurrences(probe: str, copy: Path, scip: Path) -> list[dict]:
    docs, defs = read_scip(scip)
    packages = {s.split(" ")[2] for s in defs if len(s.split(" ")) > 3 and s.startswith("rust-analyzer cargo ")}
    sources: dict[str, list[str]] = {}
    found = []
    for file, items in sorted(docs.items()):
        lines = sources.setdefault(file, (copy / file).read_text(encoding="utf-8", errors="replace").splitlines())
        local_defs = {symbol: (file, line) for line, _, _, symbol, roles in items if roles & 1 and symbol.startswith("local ")}
        for line, c0, c1, symbol, roles in sorted(items):
            if roles & 1 or not symbol or line >= len(lines):
                continue
            raw = lines[line].encode()
            if not CALLED.match(raw[c1:].decode(errors="replace")):
                continue
            where = [(f, ln) for f, ln, _ in defs.get(symbol, ())]
            if symbol.startswith("local "):
                kind, where = "local", [local_defs[symbol]] if symbol in local_defs else []
            elif where:
                kind = "repo"
            elif len(symbol.split(" ")) > 3 and symbol.split(" ")[2] in packages:
                kind = "lost"  # a symbol of a package of the repo that has no definition in the index
            else:
                kind = "external"
            found.append(
                {
                    "file": file,
                    "line": line + 1,
                    "c0": c0,
                    "c1": c1,
                    "text": raw[c0:c1].decode(errors="replace"),
                    "kind": kind,
                    "defs": sorted({(f, ln + 1) for f, ln in where}),
                }
            )
    return found


def runtime_answers(probe: str, copy: Path) -> list[dict]:
    out = []
    lines = None
    for line, text, answer in RUNTIME[probe]:
        file = "src/lib.rs"
        lines = (copy / file).read_text().splitlines()
        c0 = lines[line - 1].index(text)
        kind, _, rest = answer.partition(":")
        where = [(rest.rsplit(":", 1)[0], int(rest.rsplit(":", 1)[1]))] if kind == "repo" else []
        out.append({"file": file, "line": line, "c0": c0, "c1": c0 + len(text), "text": text, "kind": kind, "defs": where})
    return out


def apply_overrides(probe: str, found: list[dict]) -> None:
    """Replace the answers rust-analyzer could only give as a list of definitions (see OVERRIDES), and stop on any
    reference it still gives as one."""
    seen: dict[tuple, int] = defaultdict(int)
    wanted = dict(OVERRIDES.get(probe, {}))
    for call in found:
        key = (call["file"], call["line"], call["text"])
        n = seen[key]
        seen[key] += 1
        answer = wanted.pop((*key, n), None)
        if answer is None:
            if call["kind"] == "repo" and len(call["defs"]) > 1:
                sys.exit(f"{probe}: {key} #{n} is a list of definitions {call['defs']}; add it to OVERRIDES")
            continue
        kind, _, rest = answer.partition(":")
        if kind == "repo":
            target = (rest.rsplit(":", 1)[0], int(rest.rsplit(":", 1)[1]))
            if call["defs"] and target not in [tuple(d) for d in call["defs"]]:
                sys.exit(f"{probe}: override {answer} for {key} is not one of rust-analyzer's definitions {call['defs']}")
            call["kind"], call["defs"], call["proof"] = "repo", [target], "runtime"
        else:
            call["kind"], call["defs"], call["proof"] = kind, [], "runtime"
    if wanted:
        sys.exit(f"{probe}: overrides match no reference: {sorted(wanted)}")


def apply_proven(probe: str, found: list[dict]) -> None:
    """Replace the answers the crate's assertions prove to be different from rust-analyzer's (see PROVEN)."""
    seen: dict[tuple, int] = defaultdict(int)
    wanted = dict(PROVEN.get(probe, {}))
    for call in found:
        key = (call["file"], call["line"], call["text"])
        n = seen[key]
        seen[key] += 1
        answer = wanted.pop((*key, n), None)
        if answer is None:
            continue
        kind, _, rest = answer.partition(":")
        call["kind"] = kind
        call["defs"] = [(rest.rsplit(":", 1)[0], int(rest.rsplit(":", 1)[1]))] if kind == "repo" else []
        call["proof"] = "runtime"
    if wanted:
        sys.exit(f"{probe}: proven answers match no reference: {sorted(wanted)}")


TEST_ATTRIBUTE = re.compile(r"#\s*\[\s*(::\s*)?(tokio\s*::\s*)?test\b")  # `#[test]`, `#[tokio::test]`, `#[::tokio::test(..)]`


def has_tests(copy: Path) -> bool:
    return any(TEST_ATTRIBUTE.search(p.read_text(errors="replace")) for p in copy.rglob("*.rs") if not any(q.name == "target" and (q.parent / "Cargo.toml").is_file() for q in p.parents))


def build_output(directory: str, names: list[str]) -> list[str]:
    """What the copy leaves out: a Cargo.lock, and a `target` directory with a Cargo.toml beside it (a build's output; one with none
    beside it is a package's directory, as in the probe trust_target_dir_config)."""
    return [n for n in names if n == "Cargo.lock" or (n == "target" and "Cargo.toml" in names)]


def probe_names(only: list[str]) -> list[str]:
    names = sorted(p.name for p in CRATES.iterdir() if p.is_dir() and not p.name.startswith("_"))
    return [n for n in names if not only or n in only]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--toolchain", default=os.environ.get("RIPPLE_RUST_TOOLCHAIN", "/tmp/rust-toolchain"))
    parser.add_argument("--out", default=str(ORACLE))
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args()
    toolchain, out = Path(args.toolchain), Path(args.out)
    env = environment(toolchain)
    version = subprocess.run(["rust-analyzer", "--version"], env=env, capture_output=True, text=True, check=False).stdout.strip()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rs-probes-") as tmp:
        work = Path(tmp) / "crates"
        shutil.copytree(CRATES, work, ignore=build_output)
        env["CARGO_TARGET_DIR"] = str(Path(tmp) / "target")
        for name in probe_names(args.only):
            copy = work / name
            here = copy / CARGO_DIR.get(name, "")
            if name in RUNTIME or name in OVERRIDES or name in PROVEN or has_tests(copy):
                run(["cargo", "test", "--offline", "--workspace"], here, env)  # the assertions that prove the answers
            if name in RUNTIME:
                found, source = runtime_answers(name, copy), "the crate's own #[test] assertions"
            else:
                run(["cargo", "check", "--offline", "--all-targets", "--workspace"], here, env)
                scip = Path(tmp) / f"{name}.scip"
                run(["rust-analyzer", "scip", ".", "--output", str(scip)], here, env)
                found, source = occurrences(name, here, scip), version
                if name in CARGO_DIR:  # the index names files from where cargo ran: name them from the probe's root
                    for call in found:
                        call["file"] = f"{CARGO_DIR[name]}/{call['file']}"
                        call["defs"] = [(f"{CARGO_DIR[name]}/{f}", ln) for f, ln in call["defs"]]
                apply_overrides(name, found)
                apply_proven(name, found)
            # a probe answered by assertions lists only the sites they prove: a call it does not list has no answer
            document = {"probe": name, "source": source, "complete": name not in RUNTIME, "calls": found}
            (out / f"{name}.json").write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
            print(f"{name}: {len(found)} references")


if __name__ == "__main__":
    main()
