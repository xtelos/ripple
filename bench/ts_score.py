"""Score the TypeScript graph against bench/ground_truth_ts.json.

    python -m bench.ts_score                    print the report, exit 1 if below baseline
    python -m bench.ts_score --write-baseline   record the current numbers as the floor

It indexes bench/fixtures/ts/warehouse-ts with the TYPESCRIPT profile (this needs tree-sitter, the
`typescript` extra, and no node) and compares the edges ripple found with the truth, per edge kind.

For `calls`, an edge is a (caller, callee) pair of symbol ids, whatever kind ripple gave it (a call, a
constructor, an override, a callback, a decorator): the truth counts what runs.
  precision  of the edges ripple reports, the share that are expected. Every other edge is a false
             positive, and two kinds are called out because the truth names them: a caller the truth lists
             as `unresolved` (it calls the callee at run time but ripple must not claim to know that) and a
             `non_edges` pair (a pair that looks like a call and is not one).
  recall     of the expected edges, the share ripple reports. Each miss is listed with ripple's reason,
             taken from the call sites it left unresolved.
An edge from a symbol to itself is not scored, as the tracer does not record one.

`renders` and `renderer` are the JSX kinds, scored the same way against their own edges. `renders` is the creator
(the function that writes `<Child />`) and `renderer` the component React was rendering when it ran Child, which ripple
reports only where the source proves it: a missing renderer edge is recall lost, never an error, and a renderer edge the
truth does not list is a precision failure. A `calls` edge is any edge that is neither of the two.

Precision is held at exactly 1.000 for every kind whatever the baseline file says, and a kind whose recall floor is missing or null in it is an error: ripple never guesses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from bench import ts_truth
from ripple.indexer import build_graph
from ripple.jsx_facts import JSX_NO_RENDERER
from ripple.model import Graph
from ripple.profile import TYPESCRIPT
from ripple.ts_indexer import TypeScriptNotInstalled

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline_ts.json"
JSX = ("renders", "renderer")

Edge = tuple[str, str]


def predicted_edges(graph: Graph, kind: str = "calls") -> set[Edge]:
    """The (caller, callee) pairs of one kind. Every edge that is not a JSX edge is a call, whatever kind it carries."""
    wanted = (lambda k: k == kind) if kind in JSX else (lambda k: k not in JSX)
    return {(e.caller, e.callee) for e in graph.edges if e.caller != e.callee and wanted(e.kind)}


def miss_reason(graph: Graph, caller: str, callee: str, kind: str = "calls") -> str:
    """Why ripple did not report caller -> callee: the reason of an unresolved call site in the caller that
    names the callee, else a statement that it saw nothing to resolve.

    A renderer is not the function that wrote the element, so for that kind the unresolved element is found by the
    callee's name alone: the reason says why no renderer was proven for it."""
    name = re.split(r"[.:]", callee)[-1]
    if kind == "renderer":
        elements = [u for u in graph.unresolved if re.split(r"[.:]", u.text)[-1] == name and u.reason.startswith(JSX_NO_RENDERER)]
        if elements:
            return f"unresolved at {elements[0].file}:{elements[0].line}: <{elements[0].text}>: {elements[0].reason}"
        return "no element naming it was left without a renderer: it was not read as an element at all"
    if " > " in callee:
        name = callee.split(" > ")[-1]
    if name == "constructor":  # a constructor is called by the class's name
        name = re.split(r"[.:]", callee)[-2] if "." in callee else name
    sites = [u for u in graph.unresolved if u.caller == caller and re.split(r"[.:]", u.text)[-1].split("[")[0] == name]
    if sites:
        return f"unresolved at line {sites[0].line}: {sites[0].text}: {sites[0].reason}"
    if caller not in graph.symbols:
        return "the caller is not a symbol of the graph"
    return (
        "no unresolved call site names it: a getter or setter reached through a receiver of unknown class is not "
        "recorded at all, because most property reads are plain values and not calls"
    )


def score(graph: Graph, truth: dict) -> dict:
    """The scores and everything that explains them, for each edge kind in the truth."""
    sets = ts_truth.edge_sets(truth)
    kinds: dict = {}
    for kind in ts_truth.KINDS:
        s = sets[kind]
        found = predicted_edges(graph, kind)
        expected = s["expected"]
        hit = found & expected
        extra = found - expected
        kinds[kind] = {
            "status": "scored",
            "expected": len(expected),
            "reported": len(found),
            "true_positives": len(hit),
            "precision": len(hit) / len(found) if found else None,
            "recall": len(hit) / len(expected) if expected else None,
            # an unresolved caller or a non-edge that ripple reported is a precision failure, named as such
            "claimed_unresolved": sorted(found & s["unresolved"]),
            "claimed_non_edges": sorted(found & s["non_edge"]),
            "extra": sorted(extra),
            "missed": [{"edge": e, "reason": miss_reason(graph, *e, kind=kind)} for e in sorted(expected - found)],
        }
    return {"kinds": kinds}


def fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def report(result: dict) -> str:
    lines = ["ripple TypeScript benchmark: bench/fixtures/ts/warehouse-ts against bench/ground_truth_ts.json", ""]
    lines.append(f"{'kind':<9} {'expected':>8} {'reported':>8} {'correct':>7} {'precision':>10} {'recall':>7}")
    for kind, k in result["kinds"].items():
        lines.append(
            f"{kind:<9} {k['expected']:>8} {k['reported']:>8} {k['true_positives']:>7} {fmt(k['precision']):>10} {fmt(k['recall']):>7}"
        )
    for kind, k in result["kinds"].items():
        lines += ["", f"[{kind}]", f"unresolved callers ripple reported as edges: {len(k['claimed_unresolved'])}"]
        lines += [f"  claimed: {a} -> {b}" for a, b in k["claimed_unresolved"]]
        lines += [f"listed non-edges ripple reported as edges: {len(k['claimed_non_edges'])}"]
        lines += [f"  claimed: {a} -> {b}" for a, b in k["claimed_non_edges"]]
        lines += [f"other edges that are not expected: {len(k['extra']) - len(k['claimed_unresolved']) - len(k['claimed_non_edges'])}"]
        named = set(k["claimed_unresolved"]) | set(k["claimed_non_edges"])
        lines += [f"  extra: {a} -> {b}" for a, b in k["extra"] if (a, b) not in named]
        lines += [f"expected edges ripple missed: {len(k['missed'])}"]
        for miss in k["missed"]:
            lines.append(f"  missed: {miss['edge'][0]} -> {miss['edge'][1]}")
            lines.append(f"          {miss['reason']}")
    return "\n".join(lines)


def below_baseline(result: dict, baseline: dict) -> list[str]:
    """What is worse than the floor: a precision or recall under its baseline (a kind with no baseline entry must
    still have precision 1.000), and any edge that the truth says ripple must not claim."""
    failures = []
    for kind, k in result["kinds"].items():
        floors = baseline.get(kind)
        # Precision is held at 1.000 whatever the file says. A kind with no recall floor in it, or a null one, would turn
        # its floor off, so it is an error, not a pass.
        if not isinstance(floors, dict) or not isinstance(floors.get("recall"), (int, float)):
            failures.append(f"{kind}: the baseline has no recall floor for it (a missing or null entry), so nothing is held")
            floors = {}
        for metric, floor in (("precision", 1.0), ("recall", floors.get("recall"))):
            if floor is not None and k[metric] is not None and k[metric] < floor - 1e-9:
                failures.append(f"{kind} {metric} {fmt(k[metric])} < baseline {fmt(floor)}")
        for what, key in (("an unresolved caller", "claimed_unresolved"), ("a listed non-edge", "claimed_non_edges")):
            if k[key]:
                failures.append(f"{kind}: {len(k[key])} edges that are {what} in the truth (ripple must not claim them)")
    return failures


def run() -> dict:
    graph = build_graph(ts_truth.FIXTURE, TYPESCRIPT)
    return score(graph, ts_truth.load())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.ts_score")
    parser.add_argument("--write-baseline", action="store_true", help="save current numbers as the floor")
    args = parser.parse_args(argv)
    try:
        result = run()
    except TypeScriptNotInstalled as error:
        print(error, file=sys.stderr)
        return 2
    print(report(result))
    # a kind that reported no edge has no measured precision: its floor is 1.000 all the same, never null
    floors = {kind: {"precision": 1.0, "recall": k["recall"] if k["recall"] is not None else 0.0} for kind, k in result["kinds"].items()}
    if args.write_baseline:
        BASELINE.write_text(json.dumps(floors, indent=2) + "\n")
        print(f"\nbaseline written to {BASELINE}")
        return 0
    failures = below_baseline(result, json.loads(BASELINE.read_text()))
    if failures:
        print("\nBELOW BASELINE:\n  " + "\n  ".join(failures))
        return 1
    print("\nat or above baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
