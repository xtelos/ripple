"""CommonJS (`require`, `module.exports`, `exports.x`) against node itself, over the probe repos in tests/fixtures/js_probes/cjs.

Each probe is a small repo whose functions return their own ids, written by make_cjs_probes.py. Every call site it asks about is a
line that ends in `// site sN`, and expected.json beside it says what node reached when that call ran (capture_cjs_expected.mjs, run
by hand with node 24; this test needs no node): the id a function returned, or that the call threw. ripple may be less certain than
node, never more:

  - a site where node reached a repo function has an edge to that function or none at all, and is never a call to code outside the
    repo (an unresolved call, or the call to the right function, are the only answers);
  - a site where node threw, or reached something that is no repo function (a package, a builtin, a number), has no edge;
  - PROBE.json "edge" names the sites ripple must draw the edge for (the exports are provably closed), and "refuse" the sites it
    must leave unresolved with a reason that says why (a part of the reason is given): each shape that opens or changes the
    exports, or rebinds `require`, `module` or `exports`.

An outcome at a site is read off the graph by its line: an edge, an unresolved call, or neither (a call outside the repo).
"""

import filecmp
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cache import load_repo  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "js_probes"
PROBES = FIXTURES / "cjs"
NAMES = sorted(p.name for p in PROBES.iterdir() if p.is_dir())


def read(name: str) -> tuple[Path, dict, dict]:
    root = PROBES / name
    return root, json.loads((root / "PROBE.json").read_text()), json.loads((root / "expected.json").read_text())


def is_repo_label(reached: str) -> bool:
    return not reached.startswith("node_modules/")


def judge(graph, file: str, site: str, found: dict, probe: dict) -> list[str]:
    """What is wrong with ripple's outcome at one call site, as messages."""
    key = f"{file}:{site}"
    line = found["line"]
    edges = [e for e in graph.edges if e.file == file and e.line == line and e.kind in ("call", "constructor")]
    left_open = [u for u in graph.unresolved if u.file == file and u.line == line]
    callees = sorted({e.callee for e in edges})
    reached = found.get("reached")
    shown = f"{key} (line {line}): node {'reached ' + reached if reached else 'threw ' + found['threw'] if 'threw' in found else 'returned a ' + found['value']}, ripple edges {callees}, unresolved {[u.reason for u in left_open]}"
    wrong = []
    if reached is not None and is_repo_label(reached):
        if any(c != reached for c in callees):
            wrong.append(f"a WRONG EDGE: {shown}")
        if not edges and not left_open:
            wrong.append(f"a WRONG EXTERNAL (no edge, no unresolved call, so counted as outside the repo): {shown}")
    elif edges:
        wrong.append(f"a WRONG EDGE where node reached no repo function: {shown}")
    if key in probe["edge"] and not (reached is not None and callees == [reached]):
        wrong.append(f"no edge where the exports are closed: {shown}")
    part = probe["refuse"].get(key)
    if part is not None:
        if edges:
            wrong.append(f"an edge where ripple must refuse: {shown}")
        elif not any(part.lower() in u.reason.lower() for u in left_open):
            wrong.append(f"the unresolved call does not say {part!r}: {shown}")
    return wrong


@pytest.mark.parametrize("name", NAMES)
def test_ripple_never_claims_more_than_node_reached(name, tmp_path):
    root, probe, expected = read(name)
    graph = load_repo(root, cache_dir=tmp_path, lang="auto").graph
    wrong = []
    for key, found in expected["sites"].items():
        file, site = key.rsplit(":", 1)
        wrong += judge(graph, file, site, found, probe)
    for key in [*probe["edge"], *probe["refuse"]]:
        assert key in expected["sites"], f"{name}: {key} is not a site node ran: run capture_cjs_expected.mjs"
    assert not wrong, f"{name}: {len(wrong)} wrong\n" + "\n".join(wrong)


def test_the_probes_cover_every_shape_and_node_ran_them_all():
    sites = [(name, key, found) for name in NAMES for key, found in read(name)[2]["sites"].items()]
    assert len(NAMES) >= 100 and len(sites) >= 110, (len(NAMES), len(sites))
    assert {read(name)[2]["node"] for name in NAMES} == {"v24.0.1"}
    never_ran = [f"{name} {key}: {found['threw']}" for name, key, found in sites if str(found.get("threw", "")).startswith("not exported")]
    assert not never_ran, never_ran
    edges = sum(len(read(name)[1]["edge"]) for name in NAMES)
    refused = sum(len(read(name)[1]["refuse"]) for name in NAMES)
    assert edges >= 25 and refused >= 55, (edges, refused)
    reached = {found["reached"] for _, _, found in sites if "reached" in found}
    assert any(r.startswith("node_modules/") for r in reached), "no probe reaches a package"
    assert any("threw" in found for _, _, found in sites), "no probe has a call that throws"


def test_the_committed_probes_are_exactly_what_the_maker_writes(tmp_path):
    """A probe is never edited by hand: make_cjs_probes.py rewrites every file of every probe but expected.json."""
    subprocess.run([sys.executable, str(FIXTURES / "make_cjs_probes.py"), str(tmp_path)], check=True, capture_output=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == NAMES

    def compare(left: Path, right: Path) -> list[str]:
        diff = filecmp.dircmp(left, right, ignore=["expected.json"])
        found = [f"only in the maker's: {n}" for n in diff.left_only] + [f"only committed: {n}" for n in diff.right_only] + [f"differs: {n}" for n in diff.diff_files]
        for sub in diff.common_dirs:
            found += [f"{sub}/{x}" for x in compare(left / sub, right / sub)]
        return found

    wrong = [f"{name}: {message}" for name in NAMES for message in compare(tmp_path / name, PROBES / name)]
    assert not wrong, "\n".join(wrong)


def test_every_site_line_holds_one_call_and_the_marker_names_an_exported_function():
    """The line of a site is how the graph is read, so the line must be the site's alone."""
    for name in NAMES:
        root, _, expected = read(name)
        for key, found in expected["sites"].items():
            file, site = key.rsplit(":", 1)
            text = (root / file).read_text().splitlines()[found["line"] - 1]
            assert text.rstrip().endswith(f"// site {site}"), f"{name} {key}: line {found['line']} is {text!r}"
            assert re.search(rf"\bs{site[1:]}\b", text) or "return" in text or "export function" in text, f"{name} {key}: {text!r}"
