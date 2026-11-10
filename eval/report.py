"""Turn a list of run records into the per-mode and per-task summary tables.

A run the agent never got to finish because the subscription refused it
part way (rate limit, usage limit) is marked aborted and left out of every
success and median number here, so a partial batch reports only the runs that
actually happened; the summary counts the aborted runs and the table says how
many. A run marked invalid is left out the same way, in three cases: a
with-ripple run whose ripple server never connected (it ran without ripple), a
run that changed no file and had an edit tool refused by the permissions (the
harness blocked the tool it was told to use), and a run the CLI refused before
the agent did anything (an error result with no tool call and at most one turn,
reason "the agent did not run: <message>", for example a spent usage limit).
Other refusals do not invalidate a run. A run that is both aborted and invalid
is counted as aborted only. Invalid runs are reported as counts, per mode, per
task and with their reasons, never hidden.
"""

from __future__ import annotations

from collections import Counter
from statistics import median

MODES = ("with-ripple", "without")


def input_tokens(run: dict) -> int | None:
    """All input the model read: fresh, cache writes and cache reads."""
    usage = run.get("usage")
    if not usage:
        return None
    return sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return median(values) if values else None


def summarize(runs: list[dict]) -> dict:
    aborted = [r for r in runs if r.get("aborted")]
    invalid = [r for r in runs if r.get("invalid") and not r.get("aborted")]
    counted = [r for r in runs if not r.get("aborted") and not r.get("invalid")]
    modes = {}
    for mode in MODES:
        mine = [r for r in counted if r["mode"] == mode]
        spoiled = sum(1 for r in invalid if r["mode"] == mode)
        if not mine and not spoiled:
            continue
        wins = sum(1 for r in mine if r["success"])
        modes[mode] = {
            "runs": len(mine),
            "invalid": spoiled,
            "successes": wins,
            "success_rate": wins / len(mine) if mine else None,
            "median_input_tokens": _median([input_tokens(r) for r in mine]),
            "median_output_tokens": _median([(r.get("usage") or {}).get("output_tokens") for r in mine]),
            "median_turns": _median([r.get("num_turns") for r in mine]),
            "median_wall_s": _median([r.get("wall_s") for r in mine]),
            "median_cost_usd": _median([r.get("cost_usd") for r in mine]),
            "timeouts": sum(1 for r in mine if r.get("timed_out")),
            "ripple_calls": sum(r.get("ripple_calls", 0) for r in mine),
        }
        # only for runs that recorded their ripple exchanges (every run since the harness began to), so the
        # summary of an older batch is what it always was
        if any("ripple_exchanges" in r for r in mine):
            modes[mode]["ripple_error_runs"] = sum(
                1 for r in mine if any(e.get("is_error") for e in r.get("ripple_exchanges") or [])
            )
    tasks = {}
    for r in counted:
        cell = tasks.setdefault(r["task"], {}).setdefault(r["mode"], {"runs": 0, "successes": 0})
        cell["runs"] += 1
        cell["successes"] += 1 if r["success"] else 0
    for r in invalid:
        cell = tasks.setdefault(r["task"], {}).setdefault(r["mode"], {"runs": 0, "successes": 0})
        cell["invalid"] = cell.get("invalid", 0) + 1
    reasons = Counter(r["invalid"] for r in invalid)
    return {
        "modes": modes,
        "tasks": dict(sorted(tasks.items())),
        "aborted": len(aborted),
        "invalid": len(invalid),
        "invalid_reasons": dict(sorted(reasons.items())),
    }


def _num(value, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def table(summary: dict, arm: str | None = None) -> str:
    """The summary as markdown. A batch's arm (its meta's `arm`, a label like `base` or `forced+restricted`) goes on
    a line above the first table; results written before the arm existed pass none and print no such line."""
    errors = any("ripple_error_runs" in m for m in summary["modes"].values())  # batches that recorded exchanges
    lines = [f"arm: {arm}", ""] if arm else []
    lines += [
        "| Mode | Runs | Success | Median input tokens | Median output tokens | Median turns | Median wall s | Median cost-equivalent $ | ripple calls | Invalid (not counted) |"
        + (" Runs with a ripple error |" if errors else ""),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|" + ("---:|" if errors else ""),
    ]
    for mode, m in summary["modes"].items():
        wins = f"{m['successes']}/{m['runs']} ({m['success_rate']:.0%})" if m["runs"] else "n/a"
        lines.append(
            f"| {mode} | {m['runs']} | {wins} "
            f"| {_num(m['median_input_tokens'])} | {_num(m['median_output_tokens'])} | {_num(m['median_turns'], 1)} "
            f"| {_num(m['median_wall_s'], 1)} | {_num(m['median_cost_usd'], 3)} | {m['ripple_calls']} | {m.get('invalid', 0)} |"
            + (f" {m.get('ripple_error_runs', 0)} |" if errors else "")
        )
    lines += ["", "| Task | with-ripple | without |", "|---|---:|---:|"]
    for task, cells in summary["tasks"].items():
        row = [
            f"{cells[m]['successes']}/{cells[m]['runs']}" + (f" (+{cells[m]['invalid']} invalid)" if cells[m].get("invalid") else "")
            if m in cells
            else "-"
            for m in MODES
        ]
        lines.append(f"| {task} | {row[0]} | {row[1]} |")
    if summary["aborted"]:
        lines += ["", f"{summary['aborted']} run(s) aborted by a rate limit are not counted."]
    if summary.get("invalid"):
        why = "; ".join(f"{n} x {reason}" for reason, n in summary.get("invalid_reasons", {}).items())
        lines += ["", f"{summary['invalid']} run(s) marked invalid are not counted: {why}."]
    return "\n".join(lines)
