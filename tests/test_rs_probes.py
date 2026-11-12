"""The Rust differential probe suite: every call ripple settles is checked against what the compiler's tooling says.

bench/fixtures/rust-probes/crates holds small crates that pass `cargo check` (and `cargo test` where a crate
proves its answers by assertions), each built to put one rule of Rust's name resolution in front of the resolver:
every `use` form, shadowing, enum variants and tuple structs, type aliases, `Self`, trait and type qualified
calls, generic and blanket impls, cfg twins, `#[path]`, `extern crate`, renamed dependencies, editions, macros that
define names and standard macros with blocks in their arguments, files shared by several crates, and manifests
cargo would reject. bench/fixtures/rust-probes/oracle holds, for each, the callee rust-analyzer resolved at every
call site (bench/rs_probe_oracle.py writes it, by hand, with a Rust toolchain; this test needs none).

For every call site ripple settled:
  * an edge must be the oracle's callee for that site (a trait declaration is also right when the oracle's impl is
    among the override edges ripple gives at the site), and an edge where the oracle has no answer is wrong too;
  * a call counted as outside the repo must not have the oracle's callee in the repo, or be a local value.
What ripple leaves unresolved is not checked here: recall is not asserted, only that nothing it says is false.
"""

import json
from collections import Counter

import pytest

pytest.importorskip("tree_sitter_rust")

from conftest import FIXTURES  # noqa: E402
from ripple import rs_indexer as rs  # noqa: E402
from ripple.indexer import walk_languages  # noqa: E402
from ripple.profile import RUST  # noqa: E402

PROBES = FIXTURES / "rust-probes"
CRATES = PROBES / "crates"
ORACLE = PROBES / "oracle"
NAMES = sorted(p.stem for p in ORACLE.glob("*.json"))


def read(probe):
    files, skipped = walk_languages(CRATES / probe, [RUST])
    return rs.read_repo(CRATES / probe, files[RUST.name], skipped)


def column(starts, offset):
    """(line, column) of a byte offset, given the byte offset every line starts at."""
    low, high = 0, len(starts)
    while low < high - 1:
        middle = (low + high) // 2
        if starts[middle] <= offset:
            low = middle
        else:
            high = middle
    return low, offset - starts[low]


class Probe:
    def __init__(self, name):
        self.name = name
        self.index, self.graph = read(name)
        oracle = json.loads((ORACLE / f"{name}.json").read_text())
        self.complete = oracle.get("complete", True)
        self.answers = {}
        for call in oracle["calls"]:
            self.answers.setdefault((call["file"], call["line"] - 1, call["c1"]), []).append(call)
        self.starts = {}

    def at(self, file, offset):
        if file not in self.starts:
            data = (CRATES / self.name / file).read_bytes()
            self.starts[file] = [0] + [i + 1 for i, byte in enumerate(data) if byte == 10]
        line, col = column(self.starts[file], offset)
        return line, col

    def where(self, callee_key):
        symbol = self.graph.symbols[self.index.final[callee_key]]
        return symbol.file, symbol.line

    def judge(self):
        """The wrong answers ripple gives at this probe's call sites, as strings, and what was checked."""
        wrong, checked = [], Counter()
        for file, _, _, result, _, line, written, name_end in self.index.resolved_sites():
            kind = result[0]
            if kind not in ("edge", "external"):
                continue
            row, col = self.at(file, name_end)
            answers = self.answers.get((file, row, col), [])
            site = f"{file}:{line} `{written}`"
            if kind == "edge":
                targets = {self.where(result[1]), *(self.where(o) for o in result[2])}
                if not answers and not self.complete:
                    continue
                if not answers:
                    wrong.append(f"{site}: an edge to {sorted(targets)} where the oracle has no answer")
                elif not any(call["kind"] == "repo" and any(tuple(d) in targets for d in call["defs"]) for call in answers):
                    said = [(call["kind"], call["defs"]) for call in answers]
                    wrong.append(f"{site}: an edge to {sorted(targets)}, the oracle says {said}")
                else:
                    checked["edge"] += 1
            else:
                bad = []
                for call in answers:
                    if call["kind"] == "external" or (call["kind"] == "lost" and result[1] == rs.DERIVED) or (call["kind"] == "foreign" and result[1] == rs.FOREIGN):
                        continue
                    bad.append((call["kind"], call["defs"]))
                if bad:
                    wrong.append(f"{site}: counted outside the repo ({result[1][:50]}), the oracle says {bad}")
                else:
                    checked["external" if answers else "external, no oracle answer"] += 1
        return wrong, checked


@pytest.fixture(scope="module")
def totals():
    return Counter()


@pytest.mark.parametrize("name", NAMES)
def test_probe_agrees_with_the_compiler(name, totals):
    wrong, checked = Probe(name).judge()
    totals.update(checked)
    assert not wrong, f"{name}:\n  " + "\n  ".join(wrong)


def test_the_probes_are_not_vacuous(totals):
    """A resolver that settles nothing agrees with everything: the suite must have seen edges and externals."""
    if len(totals) == 0:
        pytest.skip("run with the whole module")
    assert totals["edge"] >= 250, totals
    assert totals["external"] >= 40, totals


def test_every_hand_written_oracle_answer_is_in_its_file_and_has_a_runtime_assertion_behind_it():
    """bench/rs_probe_oracle.py gives a few answers by hand (a function rust-analyzer names by path alone, a macro's
    function, a proc macro's rewrite): each is proven by the `#[test]`s of its crate, which the generator runs, so a crate
    with a hand-written answer must have tests, and every answer must have reached the oracle file marked as proven."""
    from bench import rs_probe_oracle as generator

    hand = {name: len(generator.OVERRIDES.get(name, {})) + len(generator.PROVEN.get(name, {})) for name in {*generator.OVERRIDES, *generator.PROVEN}}
    assert sum(hand.values()) >= 35, hand
    for name, count in sorted(hand.items()):
        sources = [p for p in (CRATES / name).rglob("*.rs") if "target" not in p.relative_to(CRATES / name).parts]
        assert any("#[test]" in p.read_text() for p in sources), f"{name} has hand-written answers and no #[test]"
        proven = [c for c in json.loads((ORACLE / f"{name}.json").read_text())["calls"] if c.get("proof") == "runtime"]
        assert len(proven) == count, f"{name}: {count} hand-written answers, {len(proven)} in the oracle"
    assert set(generator.RUNTIME) <= set(NAMES)


def test_every_crate_has_an_oracle():
    crates = sorted(p.name for p in CRATES.iterdir() if p.is_dir() and not p.name.startswith("_"))
    assert crates == NAMES
    assert len(crates) >= 100
