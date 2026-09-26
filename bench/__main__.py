"""Score ripple's answers against the hand-written ground truth.

    python -m bench                   print the table, exit 1 if below baseline
    python -m bench --write-baseline  record the current numbers as the floor

Precision: of the symbols ripple reported, how many are right.
Recall: of the symbols that are right, how many ripple reported.
Both are micro-averaged: every expected or reported symbol counts once.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ripple.indexer import build_graph
from ripple.queries import callees, callers, impact

HERE = Path(__file__).resolve().parent
TRUTH = HERE / "ground_truth.json"
BASELINE = HERE / "baseline.json"
GROUP_ORDER = ["imports", "methods", "typed receivers", "callees", "decorators", "dynamic", "impact"]


def predict(graph, query) -> set[str]:
    if query["kind"] == "callers":
        result = callers(graph, query["symbol"])
        return {c["symbol"] for c in result.get("callers", [])}
    if query["kind"] == "callees":
        result = callees(graph, query["symbol"])
        return {c["symbol"] for c in result.get("callees", [])}
    result = impact(graph, query["symbol"], depth=query["depth"])
    found = {t["symbol"] for t in result.get("tests", [])}
    for entries in result.get("by_file", {}).values():
        found |= {e["symbol"] for e in entries}
    return found


def ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def score() -> dict:
    queries = json.loads(TRUTH.read_text())["queries"]
    graphs = {}
    rows = []
    for q in queries:
        if q["fixture"] not in graphs:
            graphs[q["fixture"]] = build_graph(HERE / "fixtures" / q["fixture"])
        predicted, expected = predict(graphs[q["fixture"]], q), set(q["expected"])
        rows.append(
            {
                **q,
                "tp": len(predicted & expected),
                "fp": sorted(predicted - expected),
                "fn": sorted(expected - predicted),
            }
        )
    groups = {}
    for name in GROUP_ORDER + ["overall"]:
        members = [r for r in rows if name == "overall" or r["group"] == name]
        tp = sum(r["tp"] for r in members)
        fp = sum(len(r["fp"]) for r in members)
        fn = sum(len(r["fn"]) for r in members)
        groups[name] = {
            "queries": len(members),
            "exact": sum(1 for r in members if not r["fp"] and not r["fn"]),
            "precision": ratio(tp, tp + fp),
            "recall": ratio(tp, tp + fn),
        }
    return {"rows": rows, "groups": groups}


def fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def report(result: dict) -> str:
    lines = [
        f"ripple benchmark: {result['groups']['overall']['queries']} queries over 3 fixture repos",
        "",
        f"{'group':<16} {'queries':>7} {'exact':>6} {'precision':>10} {'recall':>7}",
    ]
    for name, g in result["groups"].items():
        if name == "overall":
            lines.append("-" * 50)
        lines.append(f"{name:<16} {g['queries']:>7} {g['exact']:>6} {fmt(g['precision']):>10} {fmt(g['recall']):>7}")
    lines += ["", "Queries ripple did not answer exactly:"]
    for r in result["rows"]:
        if r["fp"] or r["fn"]:
            lines.append(f"  {r['id']:<4} {r['kind']} {r['symbol']}  ({r['note']})")
            for s in r["fn"]:
                lines.append(f"         missed: {s}")
            for s in r["fp"]:
                lines.append(f"         extra:  {s}")
    return "\n".join(lines)


def below_baseline(result: dict, baseline: dict) -> list[str]:
    failures = []
    for name, floor in baseline.items():
        current = result["groups"].get(name, {})
        for metric in ("precision", "recall"):
            if floor.get(metric) is not None and (current.get(metric) or 0.0) < floor[metric] - 1e-9:
                failures.append(f"{name} {metric} {fmt(current.get(metric))} < baseline {fmt(floor[metric])}")
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench")
    parser.add_argument("--write-baseline", action="store_true", help="save current numbers as the floor")
    args = parser.parse_args(argv)
    result = score()
    print(report(result))
    floors = {
        name: {"precision": g["precision"], "recall": g["recall"]} for name, g in result["groups"].items()
    }
    if args.write_baseline:
        BASELINE.write_text(json.dumps(floors, indent=2) + "\n")
        print(f"\nbaseline written to {BASELINE.relative_to(HERE.parent)}")
        return 0
    failures = below_baseline(result, json.loads(BASELINE.read_text()))
    if failures:
        print("\nBELOW BASELINE:\n  " + "\n  ".join(failures))
        return 1
    print("\nat or above baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
