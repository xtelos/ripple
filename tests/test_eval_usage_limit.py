"""A run the CLI refused because the account's usage limit was reached did nothing, so it is not an agent failure.

The first batch to meet it (the restricted-search arms batch, 2026-11) finished its last ten runs in one or two seconds with
num_turns 1, cost 0, is_error true and the final message below; the harness graded each as a failure on an untouched repo and
carried on. The field values here are copied from those records. Nothing here calls a model or reads a results file.
"""

from __future__ import annotations

import json
import subprocess
import types

import pytest

import eval.run as run_mod
from eval.loader import load_tasks
from eval.report import summarize, table
from eval.run import invalid_reason, is_rate_limited, parse_stream

TASKS = {t.id: t for t in load_tasks()}
PY = "01-shop-tax-args"
LIMIT = "You've hit your 5-hour limit · resets 6:30pm (America/New_York)"
# tasks 24 to 28, both modes, repeat 3: the ten runs the limit cut off before they began
TEN = [(t, m) for t in range(24, 29) for m in ("with-ripple", "without")]
CONNECTED = [{"name": "ripple", "status": "connected", "source": "dynamic"}]
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def limit_stream(mode, message=LIMIT):
    """What the claude CLI printed for one of the ten: an init event and a result of 1 turn, cost 0, is_error."""
    servers = CONNECTED if mode == "with-ripple" else []
    init = {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "mcp_servers": servers, "tools": []}
    result = {"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "total_cost_usd": 0,
              "duration_ms": 300, "result": message, "usage": ZERO_USAGE, "permission_denials": []}  # fmt: skip
    return json.dumps(init) + "\n" + json.dumps(result) + "\n"


def busy_stream(is_error, turns=20, message="done"):
    init = {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "mcp_servers": [], "tools": []}
    uses = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}] * 3
    result = {"type": "result", "subtype": "success", "is_error": is_error, "num_turns": turns, "total_cost_usd": 0.2,
              "result": message, "usage": ZERO_USAGE, "permission_denials": []}  # fmt: skip
    return "\n".join(json.dumps(e) for e in [init, *uses, result]) + "\n"


# ----------------------------------------------------------------------------------------- recognising the message


@pytest.mark.parametrize("task,mode", TEN)
def test_the_real_limit_message_is_a_rate_limit_and_the_run_is_invalid_not_a_failure(task, mode):
    parsed = parse_stream(limit_stream(mode))
    assert (parsed["is_error"], parsed["num_turns"], parsed["cost_usd"], parsed["tool_calls"]) == (True, 1, 0, {})
    assert parsed["final_message"] == LIMIT
    assert is_rate_limited(parsed, "")
    assert invalid_reason(parsed, mode) == f"the agent did not run: {LIMIT}"


@pytest.mark.parametrize(
    "message",
    [
        "You've hit your 5-hour limit · resets 6:30pm (America/New_York)",
        "You've hit your weekly limit",
        "Claude AI usage limit reached|1760000000",
        "Your limit resets 6:30pm",
        "API Error: 429 Too Many Requests",
        "API Error: Overloaded",
        "Rate limit exceeded",
        "You have exceeded your quota",
        "Credit balance is too low",
        "YOU'VE HIT YOUR 5-HOUR LIMIT",
    ],
)
def test_every_usage_limit_form_is_recognised_in_the_message_and_in_stderr(message):
    error = {"got_result": True, "is_error": True, "final_message": message}
    assert is_rate_limited(error, "")
    assert is_rate_limited({"got_result": True, "is_error": True, "final_message": "failed"}, message)
    assert is_rate_limited({"got_result": False}, message)


def test_a_normal_answer_that_mentions_a_limit_is_not_a_rate_limit():
    ok = {"got_result": True, "is_error": False, "final_message": "Renamed it; the 429 handler and the rate limit docs are untouched."}
    assert not is_rate_limited(ok, "")
    assert not is_rate_limited({"got_result": True, "is_error": True, "final_message": "tool failed"}, "")


# ------------------------------------------------------------------------------- which runs did not run at all


def test_an_error_run_with_no_tool_calls_and_one_turn_did_not_run_whatever_it_said():
    parsed = parse_stream(limit_stream("without", "Something broke before the first turn " + "x" * 200))
    reason = invalid_reason(parsed, "without")
    assert reason == "the agent did not run: Something broke before the first turn " + "x" * 82  # the first 120 characters of it
    assert not is_rate_limited(parsed, "")


def test_an_error_run_that_used_tools_or_took_many_turns_stays_a_normal_run():
    assert invalid_reason(parse_stream(busy_stream(True, turns=20, message=LIMIT)), "without") is None
    assert invalid_reason(parse_stream(busy_stream(True, turns=1, message="boom")), "without") is None  # it used tools
    no_tools = parse_stream(limit_stream("without", "boom"))
    no_tools["num_turns"] = 2
    assert invalid_reason(no_tools, "without") is None


def test_a_run_that_is_not_an_error_is_never_a_run_that_did_not_run():
    clean = parse_stream(limit_stream("without", "all done"))
    clean["is_error"] = False
    assert invalid_reason(clean, "without") is None
    assert invalid_reason(parse_stream(""), "without") is None  # no result at all: timed out or crashed, an ordinary failure


# --------------------------------------------------------------------------------------------- run_one and the batch


class Fake:
    stream = ""

    def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
        stdout.write(Fake.stream.encode())
        self.returncode, self.pid = 1, 0

    def wait(self, timeout=None):
        return 1


def one_run(monkeypatch, tmp_path, stream, mode="without", graded=None):
    Fake.stream = stream
    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": Fake}))
    checked = []

    def check(task, repo):
        checked.append(1)
        return graded or {"success": False, "failures": ["suite failed"]}

    monkeypatch.setattr(run_mod, "check_task", check)
    root = tmp_path / "root"
    root.mkdir(parents=True)
    args = types.SimpleNamespace(model="sonnet", timeout=5, ripple_python=None)
    return run_mod.run_one(TASKS[PY], mode, 1, root, tmp_path / "bin", args), checked


@pytest.mark.parametrize("mode", ["with-ripple", "without"])
def test_run_one_marks_a_limit_refusal_invalid_records_the_message_and_never_grades_it(monkeypatch, tmp_path, mode):
    record, checked = one_run(monkeypatch, tmp_path, limit_stream(mode), mode)
    assert record["invalid"] == f"the agent did not run: {LIMIT}"
    assert record["limit_hit"] == LIMIT and not record.get("aborted")
    assert record["success"] is False and record["changed_files"] == [] and checked == []


def test_a_limit_refusal_is_counted_as_invalid_and_not_in_either_success_rate(monkeypatch, tmp_path):
    runs = []
    for mode in ("with-ripple", "without"):
        record, _ = one_run(monkeypatch, tmp_path / mode, limit_stream(mode), mode)
        runs.append({**record, "repeat": 3})
    summary = summarize(runs)
    assert summary["invalid"] == 2 and all(m["runs"] == 0 and m["invalid"] == 1 for m in summary["modes"].values())
    assert "Invalid (not counted)" in table(summary) and "2 run(s) marked invalid" in table(summary)


def test_an_ordinary_failed_run_stays_a_failure(monkeypatch, tmp_path):
    record, checked = one_run(monkeypatch, tmp_path, busy_stream(False))
    assert "invalid" not in record and "limit_hit" not in record and record["success"] is False and checked == [1]


def test_an_error_run_with_twenty_turns_and_tool_calls_is_graded_like_any_other(monkeypatch, tmp_path):
    record, checked = one_run(monkeypatch, tmp_path, busy_stream(True, turns=20, message="API Error: 500 internal"))
    assert "invalid" not in record and "limit_hit" not in record and not record.get("aborted") and checked == [1]


def test_a_limit_that_cut_a_run_off_part_way_is_aborted_as_before_not_graded(monkeypatch, tmp_path):
    record, checked = one_run(monkeypatch, tmp_path, busy_stream(True, turns=39, message=LIMIT))
    assert record["aborted"] is True and record["success"] is False and checked == []


def batch(monkeypatch, tmp_path, limit_at, repeats=3, extra=()):
    """main() over task PY, both modes, with a fake run_one that hits the limit on call number limit_at (1-based)."""
    calls = []

    def fake_run(cmd, *a, **k):
        if cmd[0] == "claude":
            return subprocess.CompletedProcess(cmd, 0, stdout="0.0.0 (test)\n", stderr="")
        return subprocess.run(cmd, *a, **k)

    def fake_one(task, mode, repeat, root, bin_dir, args):
        calls.append((mode, repeat))
        base = {"task": task.id, "mode": mode, "repeat": repeat, "wall_s": 0.1, "num_turns": 9, "usage": {"input_tokens": 1}}
        if len(calls) == limit_at:
            return {**base, "num_turns": 1, "is_error": True, "final_message": LIMIT, "success": False,
                    "invalid": f"the agent did not run: {LIMIT}", "limit_hit": LIMIT}  # fmt: skip
        return {**base, "success": True}

    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "run": fake_run}))
    monkeypatch.setattr(run_mod, "run_one", fake_one)
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])
    out = tmp_path / "batch.json"
    status = run_mod.main(["--tasks", PY, "--repeats", str(repeats), "--out", str(out), *extra])
    return calls, json.loads(out.read_text()), status


def test_the_batch_stops_at_the_first_limit_message_and_starts_nothing_after_it(monkeypatch, tmp_path, capsys):
    calls, saved, _ = batch(monkeypatch, tmp_path, limit_at=3)
    assert len(calls) == 3  # six were planned: three ran, three were never started
    assert saved["meta"]["stopped_early"] is True and saved["meta"]["stopped_message"] == LIMIT
    assert len(saved["runs"]) == 3 and saved["summary"]["invalid"] == 1
    text = capsys.readouterr().out
    line = next(l for l in text.splitlines() if l.startswith("stopped early"))
    assert "3 of 6" in line and "3 not started" in line and LIMIT in line and "resume" in line.lower()


def test_an_aborted_run_stops_the_batch_too_and_its_message_is_kept(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_one(task, mode, repeat, root, bin_dir, args):
        calls.append(1)
        return {"task": task.id, "mode": mode, "repeat": repeat, "wall_s": 0.1, "success": False, "aborted": True,
                "final_message": LIMIT, "stderr_tail": ""}  # fmt: skip

    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "run": lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, stdout="0.0.0\n", stderr="")}))
    monkeypatch.setattr(run_mod, "run_one", fake_one)
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])
    out = tmp_path / "a.json"
    run_mod.main(["--tasks", PY, "--repeats", "2", "--out", str(out)])
    saved = json.loads(out.read_text())
    assert len(calls) == 1 and saved["meta"]["stopped_early"] is True and saved["meta"]["stopped_message"] == LIMIT
    assert "3 not started" in capsys.readouterr().out


def test_a_batch_that_never_hits_a_limit_records_nothing_new_and_prints_no_stop_line(monkeypatch, tmp_path, capsys):
    calls, saved, _ = batch(monkeypatch, tmp_path, limit_at=0, repeats=1)
    assert len(calls) == 2 and saved["meta"]["stopped_early"] is False and "stopped_message" not in saved["meta"]
    text = capsys.readouterr().out
    assert "stopped early" not in text and "INVALID" not in text


def test_the_summary_line_names_the_invalid_runs_prominently_when_there_are_some(monkeypatch, tmp_path, capsys):
    batch(monkeypatch, tmp_path, limit_at=2)
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("WARNING"))
    assert "1 INVALID run(s) of 2" in line and "not counted" in line and f"the agent did not run: {LIMIT}" in line
