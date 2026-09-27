"""Turn a list of run records into the per-mode and per-task summary tables.

A run the agent never got to finish because the subscription refused it
(rate limit) is marked aborted and left out of every number here, so a
partial batch reports only the runs that actually happened. A with-ripple
run whose ripple server never connected is marked invalid and left out the
same way, since it ran without ripple; both are reported as counts.
"""

from __future__ import annotations

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
        if not mine:
            continue
        wins = sum(1 for r in mine if r["success"])
        modes[mode] = {
            "runs": len(mine),
            "successes": wins,
            "success_rate": wins / len(mine),
            "median_input_tokens": _median([input_tokens(r) for r in mine]),
            "median_output_tokens": _median([(r.get("usage") or {}).get("output_tokens") for r in mine]),
            "median_turns": _median([r.get("num_turns") for r in mine]),
            "median_wall_s": _median([r.get("wall_s") for r in mine]),
            "median_cost_usd": _median([r.get("cost_usd") for r in mine]),
            "timeouts": sum(1 for r in mine if r.get("timed_out")),
            "ripple_calls": sum(r.get("ripple_calls", 0) for r in mine),
        }
    tasks = {}
    for r in counted:
        cell = tasks.setdefault(r["task"], {}).setdefault(r["mode"], {"runs": 0, "successes": 0})
        cell["runs"] += 1
        cell["successes"] += 1 if r["success"] else 0
    return {"modes": modes, "tasks": dict(sorted(tasks.items())), "aborted": len(aborted), "invalid": len(invalid)}


def _num(value, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def table(summary: dict) -> str:
    lines = [
        "| Mode | Runs | Success | Median input tokens | Median output tokens | Median turns | Median wall s | Median cost-equivalent $ | ripple calls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, m in summary["modes"].items():
        lines.append(
            f"| {mode} | {m['runs']} | {m['successes']}/{m['runs']} ({m['success_rate']:.0%}) "
            f"| {_num(m['median_input_tokens'])} | {_num(m['median_output_tokens'])} | {_num(m['median_turns'], 1)} "
            f"| {_num(m['median_wall_s'], 1)} | {_num(m['median_cost_usd'], 3)} | {m['ripple_calls']} |"
        )
    lines += ["", "| Task | with-ripple | without |", "|---|---:|---:|"]
    for task, cells in summary["tasks"].items():
        row = [f"{cells[m]['successes']}/{cells[m]['runs']}" if m in cells else "-" for m in MODES]
        lines.append(f"| {task} | {row[0]} | {row[1]} |")
    if summary["aborted"]:
        lines += ["", f"{summary['aborted']} run(s) aborted by a rate limit are not counted."]
    if summary.get("invalid"):
        lines += ["", f"{summary['invalid']} run(s) marked invalid (ripple server not connected) are not counted."]
    return "\n".join(lines)
