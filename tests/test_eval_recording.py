"""What the harness records of a run's ripple tool calls: the input of every `mcp__ripple__*` call and, from its
tool_result, the error flag, the size and the first 2000 characters, plus the ToolSearch queries. The counts that
were always recorded (`tool_calls`, `ripple_calls`) must not move. No model is called: the streams are fabricated.
"""

from __future__ import annotations

import json
import subprocess
import types

import eval.run as run_mod
from eval.loader import load_tasks
from eval.report import summarize, table
from eval.run import MAX_RECORDED_BYTES, MAX_RESULT_HEAD, parse_stream

INIT = {"type": "system", "subtype": "init", "model": "m", "tools": ["Read", "mcp__ripple__callers"],
        "mcp_servers": [{"name": "ripple", "status": "connected"}]}  # fmt: skip
RESULT = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 1000,
          "total_cost_usd": 0.1, "result": "done", "permission_denials": [],
          "usage": {"input_tokens": 1}}  # fmt: skip


def use(tool_id, name, **tool_input):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]}}


def answer(tool_id, content, **extra):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content, **extra}]}}


def stream(*events, finish=True):
    return "\n".join(json.dumps(e) for e in [INIT, *events, *([RESULT] if finish else [])]) + "\n"


def test_a_ripple_call_with_a_string_result_is_recorded_with_its_input_size_and_head():
    parsed = parse_stream(stream(use("t1", "mcp__ripple__callers", symbol="Money.format"), answer("t1", '{"count": 0}')))
    assert parsed["ripple_exchanges"] == [
        {"tool": "mcp__ripple__callers", "input": {"symbol": "Money.format"}, "is_error": False, "result_chars": 12, "result_head": '{"count": 0}'}
    ]  # fmt: skip
    assert parsed["ripple_exchanges_truncated"] is False and parsed["toolsearch_queries"] == []
    assert parsed["ripple_calls"] == 1 and parsed["tool_calls"] == {"mcp__ripple__callers": 1}


def test_a_result_that_is_a_list_of_content_blocks_is_joined_and_its_size_is_the_whole_text():
    blocks = [{"type": "text", "text": "a" * 1500}, {"type": "text", "text": "b" * 1500}]
    exchange = parse_stream(stream(use("t1", "mcp__ripple__impact", symbol="f"), answer("t1", blocks)))["ripple_exchanges"][0]
    assert exchange["result_chars"] == 3000 and len(exchange["result_head"]) == MAX_RESULT_HEAD == 2000
    assert exchange["result_head"] == "a" * 1500 + "b" * 500


def test_a_block_that_is_not_text_is_still_counted_and_does_not_break_the_parse():
    blocks = [{"type": "text", "text": "x"}, {"type": "image", "source": "..."}]
    exchange = parse_stream(stream(use("t1", "mcp__ripple__path", a="x"), answer("t1", blocks)))["ripple_exchanges"][0]
    assert exchange["result_head"].startswith("x") and exchange["result_chars"] > 1


def test_an_error_result_sets_the_flag_and_keeps_the_message():
    parsed = parse_stream(stream(use("t1", "mcp__ripple__check", tests=["x"]), answer("t1", "Error: no such tool", is_error=True)))
    exchange = parsed["ripple_exchanges"][0]
    assert exchange["is_error"] is True and exchange["result_head"] == "Error: no such tool"
    assert exchange["input"] == {"tests": ["x"]}


def test_a_call_with_no_result_because_the_run_was_killed_is_recorded_with_nothing_for_the_result():
    parsed = parse_stream(stream(use("t1", "mcp__ripple__impact", symbol="f"), finish=False))
    assert parsed["ripple_exchanges"] == [
        {"tool": "mcp__ripple__impact", "input": {"symbol": "f"}, "is_error": None, "result_chars": None, "result_head": None}
    ]  # fmt: skip
    assert parsed["ripple_calls"] == 1 and parsed["got_result"] is False


def test_several_calls_are_matched_to_their_own_results_whatever_the_order_they_come_back_in():
    events = [
        use("a", "mcp__ripple__callers", symbol="one"),
        use("b", "mcp__ripple__callers", symbol="two"),
        answer("b", "second"),
        answer("a", "first"),
        use("c", "mcp__ripple__impact", symbol="three"),
        answer("c", "third", is_error=True),
    ]
    got = [(e["input"]["symbol"], e["result_head"], e["is_error"]) for e in parse_stream(stream(*events))["ripple_exchanges"]]
    assert got == [("one", "first", False), ("two", "second", False), ("three", "third", True)]


def test_toolsearch_queries_are_recorded_and_other_tools_results_are_not():
    events = [
        use("s1", "ToolSearch", query="select:mcp__ripple__callers", max_results=5),
        answer("s1", "loaded"),
        use("r1", "Read", file_path="x.ts"),
        answer("r1", "file text"),
    ]
    parsed = parse_stream(stream(*events))
    assert parsed["toolsearch_queries"] == [{"query": "select:mcp__ripple__callers", "max_results": 5}]
    assert parsed["ripple_exchanges"] == [] and parsed["tool_calls"] == {"ToolSearch": 1, "Read": 1} and parsed["ripple_calls"] == 0


def test_an_input_is_clipped_and_a_credential_in_it_or_in_the_result_is_masked():
    secret = "ghp_abcdef123456"  # pragma: allowlist secret
    events = [use("t1", "mcp__ripple__callers", symbol="s" * 5000, note=f"GH_TOKEN={secret}"), answer("t1", f"token GH_TOKEN={secret} end")]
    exchange = parse_stream(stream(*events))["ripple_exchanges"][0]
    assert len(exchange["input"]["symbol"]) == 300
    assert secret not in json.dumps(exchange) and "[redacted]" in exchange["result_head"]


def test_the_recording_has_a_hard_cap_and_says_when_it_cut():
    # each exchange is about 2.3 KB (a 2000 character head plus its input): 40 of them are far past 40 KB
    events = []
    for i in range(40):
        events += [use(f"t{i}", "mcp__ripple__impact", symbol=f"sym{i}"), answer(f"t{i}", "x" * 5000)]
    parsed = parse_stream(stream(*events))
    kept = parsed["ripple_exchanges"]
    assert parsed["ripple_exchanges_truncated"] is True
    assert 0 < len(kept) < 40
    assert [e["input"]["symbol"] for e in kept] == [f"sym{i}" for i in range(len(kept))]  # the first ones, in order
    assert len(json.dumps(kept)) <= MAX_RECORDED_BYTES == 40_000
    assert all(e["result_chars"] == 5000 for e in kept)
    assert parsed["ripple_calls"] == 40 and parsed["tool_calls"] == {"mcp__ripple__impact": 40}  # the counts are never cut


def test_toolsearch_queries_share_the_cap_with_the_exchanges():
    events = [use(f"s{i}", "ToolSearch", query="q" * 250) for i in range(400)]
    parsed = parse_stream(stream(*events))
    assert parsed["ripple_exchanges_truncated"] is True and len(parsed["toolsearch_queries"]) < 400
    assert len(json.dumps(parsed["toolsearch_queries"])) <= MAX_RECORDED_BYTES


def test_a_stream_with_no_ripple_call_records_empty_lists_and_not_truncated():
    parsed = parse_stream(stream(use("r1", "Read", file_path="x")))
    assert parsed["ripple_exchanges"] == [] and parsed["toolsearch_queries"] == [] and parsed["ripple_exchanges_truncated"] is False


def test_a_tool_use_without_an_id_is_recorded_with_no_result_instead_of_failing():
    event = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__ripple__callers", "input": {"symbol": "f"}}]}}
    exchange = parse_stream(stream(event))["ripple_exchanges"][0]
    assert exchange["result_chars"] is None and exchange["input"] == {"symbol": "f"}


# ------------------------------------------------------------------------------------------ run record, report


class Fake:
    stream = ""

    def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
        stdout.write(Fake.stream.encode())
        self.returncode, self.pid = 0, 0

    def wait(self, timeout=None):
        return 0


def test_run_one_puts_the_recording_in_the_run_record(monkeypatch, tmp_path):
    Fake.stream = stream(use("t1", "mcp__ripple__callers", symbol="f"), answer("t1", "ok"), use("s", "ToolSearch", query="ripple"))
    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": Fake}))
    monkeypatch.setattr(run_mod, "check_task", lambda task, repo: {"success": False, "failures": []})
    root = tmp_path / "root"
    root.mkdir()
    task = next(t for t in load_tasks() if not t.spec)
    record = run_mod.run_one(task, "with-ripple", 1, root, tmp_path / "bin", types.SimpleNamespace(model="sonnet", timeout=5))
    assert record["ripple_exchanges"][0]["result_head"] == "ok" and record["toolsearch_queries"] == [{"query": "ripple"}]
    assert record["ripple_exchanges_truncated"] is False and record["ripple_calls"] == 1


def run(mode, exchanges=None, success=True):
    record = {"task": "t", "mode": mode, "success": success, "num_turns": 3, "wall_s": 1.0, "usage": {"input_tokens": 1, "output_tokens": 1}}
    if exchanges is not None:
        record["ripple_exchanges"] = exchanges
    return record


def test_the_summary_counts_runs_with_a_ripple_error_and_the_table_shows_it():
    errored, fine = {"is_error": True}, {"is_error": False}
    summary = summarize([run("with-ripple", [fine, errored]), run("with-ripple", [fine]), run("with-ripple", []), run("without", [])])
    assert summary["modes"]["with-ripple"]["ripple_error_runs"] == 1 and summary["modes"]["without"]["ripple_error_runs"] == 0
    text = table(summary)
    assert "Runs with a ripple error" in text.splitlines()[0]
    rows = {line.split("|")[1].strip(): line for line in text.splitlines()[2:4]}
    assert rows["with-ripple"].endswith("| 1 |") and rows["without"].endswith("| 0 |")


def test_records_written_before_the_recording_summarize_and_print_exactly_as_they_did():
    summary = summarize([run("with-ripple"), run("without")])
    assert all("ripple_error_runs" not in m for m in summary["modes"].values())
    assert "ripple error" not in table(summary)
