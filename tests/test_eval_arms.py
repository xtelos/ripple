"""The test arms of the agent eval: --forced-use, --restrict-search and (with --hard-prompts) their combinations. Every
flag defaults off and changes nothing then (tests/test_eval_arms_pins.py pins that). Nothing here calls a model: the agent
is a fake, and `claude --help` is what the flag syntax was checked against.

Tests over a node task skip without the fetched repository (`python -m eval.fetch`).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

import eval.run as run_mod
from eval.arms import FORCED_USE_LINE, RESTRICTED_DISALLOWED, RESTRICTED_PREAMBLE, Arms
from eval.loader import load_tasks
from eval.node_agent import node_allowed_tools, node_preamble, node_restricted_preamble, write_node_shims
from eval.report import summarize, table
from eval.run import PREAMBLE, WITH_RIPPLE_LINE, agent_command, allowed_tools, build_prompt, parse_stream

TASKS = {t.id: t for t in load_tasks()}
PY, VITEST, JEST = "01-shop-tax-args", "20-neverthrow-rename-resultasync-combine", "24-tsyringe-rename-transform"
NEEDS_CACHE = "needs the pinned repositories: python -m eval.fetch"
SHELL = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
FORCED, RESTRICT = Arms(forced_use=True), Arms(restrict_search=True)
BOTH = Arms(forced_use=True, restrict_search=True)


def fetched(*ids):
    return [pytest.param(i, id=i, marks=pytest.mark.skipif(not TASKS[i].available, reason=NEEDS_CACHE)) for i in ids]


# ------------------------------------------------------------------------------------------------- the label


def test_the_arm_label_is_base_or_the_flags_joined_with_plus():
    assert Arms().label == "base"
    assert FORCED.label == "forced" and RESTRICT.label == "restricted"
    assert BOTH.label == "forced+restricted"


def test_the_flags_default_off():
    assert not Arms().forced_use and not Arms().restrict_search


# ------------------------------------------------------------------------------------------------ --forced-use


def test_the_forced_sentence_is_exactly_the_one_asked_for():
    assert FORCED_USE_LINE == "Before editing, call the ripple `callers` tool on the symbol you are changing and use the call sites it returns."


def test_the_forced_sentence_does_not_say_every_task_is_a_rename():
    # Python tasks 01, 06, 08, 09 and 10 reorder, move or add an argument; none of them renames anything
    assert "renam" not in FORCED_USE_LINE


@pytest.mark.parametrize("task_id", [PY, *fetched(VITEST, JEST)])
def test_forced_use_adds_the_sentence_to_the_with_ripple_prompt_only(task_id):
    task = TASKS[task_id]
    plain_with, plain_without = build_prompt("T", "with-ripple", task), build_prompt("T", "without", task)
    forced_with, forced_without = build_prompt("T", "with-ripple", task, FORCED), build_prompt("T", "without", task, FORCED)
    assert forced_without == plain_without
    assert forced_with == plain_with.replace(WITH_RIPPLE_LINE + "\n\n", WITH_RIPPLE_LINE + "\n\n" + FORCED_USE_LINE + "\n\n", 1)
    assert forced_with.endswith("\n\nT") and forced_with.count(FORCED_USE_LINE) == 1
    assert FORCED_USE_LINE not in forced_without


def test_forced_use_changes_neither_tools_nor_command_apart_from_the_prompt():
    task = TASKS[PY]
    for mode in ("with-ripple", "without"):
        assert allowed_tools(mode, task, FORCED) == allowed_tools(mode, task)
        assert agent_command("P", mode, Path("/m"), "sonnet", task, FORCED) == agent_command("P", mode, Path("/m"), "sonnet", task)


# ------------------------------------------------------------------------------------------- --restrict-search


def test_the_restricted_command_disallows_the_search_tools_for_both_arms_and_nothing_else_changes():
    for mode in ("with-ripple", "without"):
        plain = agent_command("P", mode, Path("/m"), "sonnet", TASKS[PY])
        command = agent_command("P", mode, Path("/m"), "sonnet", TASKS[PY], RESTRICT)
        i = command.index("--disallowedTools")
        rules = command[i + 1].split(",")
        assert {"Grep", "Glob", "WebSearch", "WebFetch"} <= set(rules)
        assert rules == list(RESTRICTED_DISALLOWED)
        assert command[:i] + command[i + 2 :] == plain  # nothing else moved
        assert "--disallowedTools" not in plain


def test_the_shell_commands_that_search_are_disallowed_too_because_the_cli_lets_read_only_ones_through():
    shell = {r for r in RESTRICTED_DISALLOWED if r.startswith("Bash(")}
    for name in ("grep", "egrep", "fgrep", "rg", "ag", "find", "fd", "tree", "locate", "git grep", "git ls-files"):
        assert f"Bash({name}:*)" in shell, name
    assert not shell & set(allowed_tools("with-ripple", TASKS[PY]))  # no rule is both allowed and denied


def test_the_python_restricted_preamble_names_pytest_and_says_search_is_unavailable():
    assert "pytest" in RESTRICTED_PREAMBLE and "Edit or Write" in RESTRICTED_PREAMBLE
    assert re.search(r"Grep.*Glob.*not available", RESTRICTED_PREAMBLE)
    assert "tsc" not in RESTRICTED_PREAMBLE and "ripple" not in RESTRICTED_PREAMBLE.lower()
    for mode in ("with-ripple", "without"):
        prompt = build_prompt("T", mode, TASKS[PY], RESTRICT)
        assert prompt.startswith(RESTRICTED_PREAMBLE + "\n\n") and PREAMBLE not in prompt
    assert build_prompt("T", "with-ripple", None, RESTRICT) == RESTRICTED_PREAMBLE + "\n\n" + WITH_RIPPLE_LINE + "\n\nT"


@pytest.mark.parametrize("task_id", fetched(VITEST, JEST))
def test_the_node_restricted_preamble_names_only_the_runner_and_says_search_is_unavailable(task_id):
    spec = TASKS[task_id].spec
    text = node_restricted_preamble(spec)
    assert "tsc" not in text and "type check" not in text.lower()
    assert ("`vitest run`" if spec.runner == "vitest" else "`jest`") in text
    assert re.search(r"Grep.*Glob.*not available", text) and "Edit or Write" in text
    assert text != node_preamble(spec)
    for mode in ("with-ripple", "without"):
        assert build_prompt("T", mode, TASKS[task_id], RESTRICT).startswith(text + "\n\n")


@pytest.mark.parametrize("task_id", fetched(VITEST, JEST))
def test_the_restricted_node_shell_allows_the_runner_only(task_id):
    task = TASKS[task_id]
    runner = task.spec.runner
    assert node_allowed_tools(task.spec, restricted=True) == [f"Bash({runner}:*)"]
    assert allowed_tools("without", task, RESTRICT) == [f"Bash({runner}:*)"]
    assert allowed_tools("with-ripple", task, RESTRICT) == [f"Bash({runner}:*)", "mcp__ripple"]
    assert node_allowed_tools(task.spec) == [f"Bash({runner}:*)", "Bash(tsc:*)"]  # unrestricted is as it was
    command = agent_command("P", "without", Path("/m"), "sonnet", task, RESTRICT)
    assert command[command.index("--allowedTools") + 1] == f"Bash({runner}:*)"
    assert "Bash(tsc:*)" not in command[command.index("--disallowedTools") + 1].split(",")  # tsc is absent, not denied


@SHELL
@pytest.mark.parametrize("task_id", fetched(VITEST, JEST))
def test_the_restricted_run_writes_the_runner_shim_and_no_tsc_shim(task_id, tmp_path):
    spec = TASKS[task_id].spec
    write_node_shims(tmp_path / "plain", spec, Path("/repo"))
    write_node_shims(tmp_path / "restricted", spec, Path("/repo"), restricted=True)
    assert sorted(p.name for p in (tmp_path / "plain").iterdir()) == sorted([spec.runner, "tsc"])
    assert [p.name for p in (tmp_path / "restricted").iterdir()] == [spec.runner]
    assert (tmp_path / "restricted" / spec.runner).read_text() == (tmp_path / "plain" / spec.runner).read_text()


def test_a_python_task_keeps_its_shell_when_restricted():
    task = TASKS[PY]
    assert allowed_tools("without", task, RESTRICT) == allowed_tools("without", task)


# ------------------------------------------------------------------------------- the stream in a restricted run


def shell_stream(*commands):
    uses = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"b{i}", "name": "Bash", "input": {"command": c}}]}} for i, c in enumerate(commands)]
    uses.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": "x"}}]}})
    return "\n".join(json.dumps(e) for e in uses) + "\n"


def test_a_restricted_run_records_the_shell_commands_it_ran_so_a_leaked_search_shows():
    parsed = parse_stream(shell_stream("vitest run", "x" * 500, "cat a | grep b"), shell_log=True)
    assert parsed["bash_commands"][0] == "vitest run" and len(parsed["bash_commands"][1]) == 160
    assert parsed["bash_commands"][2] == "cat a | grep b"
    assert "bash_commands" not in parse_stream(shell_stream("vitest run"))


def test_the_shell_log_is_bounded():
    parsed = parse_stream(shell_stream(*[f"cmd{i}" for i in range(500)]), shell_log=True)
    assert len(parsed["bash_commands"]) == 60


# ------------------------------------------------------------------------------------- run_one and the records


class Fake:
    cmds: list = []
    stream = ""

    def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
        Fake.cmds.append(cmd)
        stdout.write(Fake.stream.encode())
        self.returncode, self.pid = 0, 0

    def wait(self, timeout=None):
        return 0


def run_one(monkeypatch, tmp_path, task, mode, arms=None, stream=""):
    Fake.cmds, Fake.stream = [], stream
    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": Fake}))
    monkeypatch.setattr(run_mod, "check_task", lambda task, repo: {"success": False, "failures": []})
    root = tmp_path / "root"
    root.mkdir()
    args = types.SimpleNamespace(model="sonnet", timeout=5, ripple_python=None, **({} if arms is None else {"arms": arms}))
    return run_mod.run_one(task, mode, 1, root, tmp_path / "bin", args), root


@pytest.mark.parametrize("arms, label", [(None, "base"), (Arms(), "base"), (FORCED, "forced"), (RESTRICT, "restricted"), (BOTH, "forced+restricted")])
def test_every_run_record_carries_its_arm(monkeypatch, tmp_path, arms, label):
    record, _ = run_one(monkeypatch, tmp_path, TASKS[PY], "with-ripple", arms)
    assert record["arm"] == label


def test_run_one_starts_the_agent_with_the_armed_prompt_and_tools(monkeypatch, tmp_path):
    record, _ = run_one(monkeypatch, tmp_path, TASKS[PY], "with-ripple", BOTH)
    (command,) = Fake.cmds
    assert command == agent_command(build_prompt(TASKS[PY].prompt, "with-ripple", TASKS[PY], BOTH), "with-ripple", Path(command[command.index("--mcp-config") + 1]), "sonnet", TASKS[PY], BOTH)
    assert FORCED_USE_LINE in command[2] and "--disallowedTools" in command


def test_the_without_run_of_a_forced_batch_is_the_plain_prompt(monkeypatch, tmp_path):
    run_one(monkeypatch, tmp_path, TASKS[PY], "without", FORCED)
    assert Fake.cmds[0][2] == build_prompt(TASKS[PY].prompt, "without", TASKS[PY])


def test_a_default_run_has_no_shell_log_and_a_restricted_one_has(monkeypatch, tmp_path):
    stream = shell_stream("grep x", "pytest")
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    plain, _ = run_one(monkeypatch, tmp_path / "a", TASKS[PY], "without", None, stream)
    restricted, _ = run_one(monkeypatch, tmp_path / "b", TASKS[PY], "without", RESTRICT, stream)
    assert "bash_commands" not in plain and restricted["bash_commands"] == ["grep x", "pytest"]


@SHELL
@pytest.mark.parametrize("task_id", fetched(VITEST))
def test_a_restricted_node_run_has_no_tsc_in_its_bin_directory(monkeypatch, tmp_path, task_id):
    record, root = run_one(monkeypatch, tmp_path, TASKS[task_id], "with-ripple", RESTRICT)
    assert sorted(p.name for p in (root / record["run_dir"] / "bin").iterdir()) == ["vitest"]
    (command,) = Fake.cmds
    assert command[command.index("--allowedTools") + 1] == "Bash(vitest:*),mcp__ripple"
    config = json.loads((root / record["run_dir"] / "mcp.json").read_text())["mcpServers"]["ripple"]
    assert config["args"][-1].endswith("/bin/vitest run")  # the check tool still runs the runner


@SHELL
@pytest.mark.parametrize("task_id", fetched(VITEST))
def test_an_unrestricted_node_run_still_has_both_shims(monkeypatch, tmp_path, task_id):
    record, root = run_one(monkeypatch, tmp_path, TASKS[task_id], "without", FORCED)
    assert sorted(p.name for p in (root / record["run_dir"] / "bin").iterdir()) == ["tsc", "vitest"]


# ------------------------------------------------------------------------- main(): meta, dry view and refusals


@pytest.fixture
def no_claude(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError(f"started something it should not have: {a}")

    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "run": refuse, "Popen": refuse}))
    monkeypatch.setattr(run_mod, "run_one", refuse)
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])


class Stop(Exception):
    pass


def meta_of(monkeypatch, argv):
    seen = {}

    def fake_run(cmd, *a, **k):
        if cmd[0] == "claude":
            return subprocess.CompletedProcess(cmd, 0, stdout="0.0.0 (test)\n", stderr="")
        return subprocess.run(cmd, *a, **k)

    def save(out, meta, runs):
        seen["meta"] = dict(meta)
        raise Stop

    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "run": fake_run}))
    monkeypatch.setattr(run_mod, "run_one", lambda *a, **k: {"success": True, "wall_s": 0.0})
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])
    monkeypatch.setattr(run_mod, "save", save)
    with pytest.raises(Stop):
        run_mod.main(argv)
    return seen["meta"]


def test_the_default_meta_gains_only_the_arm(monkeypatch, tmp_path):
    meta = meta_of(monkeypatch, ["--tasks", PY, "--repeats", "1", "--out", str(tmp_path / "x.json")])
    assert meta["arm"] == "base"
    assert meta["prompt_preamble"] == PREAMBLE and "--disallowedTools" not in meta["command"]
    assert not {"arm_flags", "forced_use_line", "disallowed_tools", "restricted_prompt_preamble"} & set(meta)


def test_a_forced_batch_records_the_flag_and_the_sentence(monkeypatch, tmp_path):
    meta = meta_of(monkeypatch, ["--tasks", PY, "--repeats", "1", "--forced-use", "--out", str(tmp_path / "x.json")])
    assert meta["arm"] == "forced" and meta["arm_flags"] == {"forced_use": True, "hard_prompts": False, "restrict_search": False}
    assert meta["forced_use_line"] == FORCED_USE_LINE and meta["prompt_preamble"] == PREAMBLE


def test_a_restricted_batch_records_the_tools_the_preamble_and_the_command(monkeypatch, tmp_path):
    meta = meta_of(monkeypatch, ["--tasks", PY, "--repeats", "1", "--restrict-search", "--out", str(tmp_path / "x.json")])
    assert meta["arm"] == "restricted" and meta["disallowed_tools"] == list(RESTRICTED_DISALLOWED)
    assert meta["prompt_preamble"] == RESTRICTED_PREAMBLE and "--disallowedTools" in meta["command"]


@pytest.mark.parametrize("task_id", fetched(VITEST))
def test_a_combined_node_batch_records_every_flag_and_the_restricted_node_preamble(monkeypatch, tmp_path, task_id):
    argv = ["--tasks", task_id, "--repeats", "1", "--forced-use", "--restrict-search", "--out", str(tmp_path / "x.json")]
    meta = meta_of(monkeypatch, argv)
    assert meta["arm"] == "forced+restricted"
    assert meta["node_prompt_preambles"] == {"vitest": node_restricted_preamble(TASKS[task_id].spec)}
    assert "Bash(tsc:*)" not in meta["node_commands"]["vitest"][meta["node_commands"]["vitest"].index("--allowedTools") + 1]


def test_forced_use_with_no_with_ripple_arm_is_refused_before_anything_starts(capsys, no_claude):
    with pytest.raises(SystemExit):
        run_mod.main(["--dry", "--tasks", PY, "--modes", "without", "--forced-use"])
    assert "--forced-use" in capsys.readouterr().err


def dry(capsys, *argv):
    assert run_mod.main(["--dry", *argv]) == 0
    return capsys.readouterr().out


def test_the_plain_dry_view_says_base(capsys, no_claude):
    out = dry(capsys, "--tasks", PY)
    assert "arm: base" in out and "--disallowedTools" not in out and FORCED_USE_LINE not in out


def test_the_forced_dry_view_shows_the_sentence_in_the_with_ripple_preamble_only(capsys, no_claude):
    out = dry(capsys, "--tasks", PY, "--forced-use")
    assert "arm: forced" in out
    section = lambda mode: out.split(f"[python] prompt before the task, {mode}:")[1].split("[python]")[0]  # noqa: E731
    assert FORCED_USE_LINE in section("with-ripple") and FORCED_USE_LINE not in section("without")
    assert WITH_RIPPLE_LINE in section("with-ripple") and PREAMBLE in section("without")


def test_the_restricted_dry_view_shows_the_preamble_and_both_tool_lists(capsys, no_claude):
    out = dry(capsys, "--tasks", PY, "--restrict-search")
    assert "arm: restricted" in out and RESTRICTED_PREAMBLE in out
    assert "[python] allowed tools, without: Bash(python -m pytest:*),Bash(python3 -m pytest:*),Bash(pytest:*)" in out
    assert "[python] allowed tools, with-ripple: Bash(python -m pytest:*),Bash(python3 -m pytest:*),Bash(pytest:*),mcp__ripple" in out
    assert out.count("disallowed tools, ") == 2 and ",".join(RESTRICTED_DISALLOWED) in out
    assert PREAMBLE not in out.replace(RESTRICTED_PREAMBLE, "")


# ------------------------------------------------------------------------------ the arm in the human-facing table


def a_record(task, mode, repeat):
    return {
        "task": task.id, "mode": mode, "repeat": repeat, "success": True, "num_turns": 3, "wall_s": 1.0,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }  # fmt: skip


def test_the_restrict_search_help_admits_the_test_file_channel(capsys):
    with pytest.raises(SystemExit):
        run_mod.main(["--help"])
    assert "writing a test file" in " ".join(capsys.readouterr().out.split())


def test_the_table_names_the_arm_on_a_line_above_the_first_table():
    summary = summarize([a_record(TASKS[PY], "without", 1)])
    for label in ("base", "forced+restricted"):
        lines = table(summary, arm=label).splitlines()
        assert lines[0] == f"arm: {label}" and lines[1] == "" and lines[2].startswith("| Mode |")


def test_a_table_with_no_arm_is_what_it_always_was():
    summary = summarize([a_record(TASKS[PY], "without", 1)])
    assert table(summary).splitlines()[0].startswith("| Mode |") and "arm:" not in table(summary)
    assert table(summary, arm=None) == table(summary)


def test_save_writes_the_arm_from_the_meta_above_the_md_table_and_none_for_an_older_meta(tmp_path):
    runs = [a_record(TASKS[PY], "without", 1)]
    run_mod.save(tmp_path / "armed.json", {"arm": "restricted"}, runs)
    run_mod.save(tmp_path / "older.json", {}, runs)
    assert (tmp_path / "armed.json").with_suffix(".md").read_text().splitlines()[0] == "arm: restricted"
    assert (tmp_path / "older.json").with_suffix(".md").read_text().startswith("| Mode |")


@pytest.mark.parametrize("argv, label", [([], "base"), (["--forced-use"], "forced")])
def test_the_printed_summary_and_the_md_carry_the_arm(monkeypatch, tmp_path, capsys, argv, label):
    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "run": lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, stdout="0.0.0\n", stderr="")}))
    monkeypatch.setattr(run_mod, "run_one", lambda task, mode, repeat, *a, **k: a_record(task, mode, repeat))
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])
    out = tmp_path / "x.json"
    assert run_mod.main(["--tasks", PY, "--repeats", "1", "--out", str(out), *argv]) == 0
    assert f"\narm: {label}\n\n| Mode |" in capsys.readouterr().out
    assert out.with_suffix(".md").read_text().splitlines()[0] == f"arm: {label}"


@SHELL
@pytest.mark.parametrize("task_id", fetched(VITEST))
def test_the_combined_node_dry_view_shows_arm_preamble_and_tools(capsys, no_claude, task_id):
    out = dry(capsys, "--tasks", task_id, "--forced-use", "--restrict-search", "--ripple-python", "/py/bin/python")
    assert "arm: forced+restricted" in out
    assert "[node, vitest] allowed tools, without: Bash(vitest:*)\n" in out
    assert "[node, vitest] allowed tools, with-ripple: Bash(vitest:*),mcp__ripple\n" in out
    section = out.split("[node, vitest] prompt before the task, with-ripple:")[1].split("[node, vitest]")[0]
    assert node_restricted_preamble(TASKS[task_id].spec) in section and FORCED_USE_LINE in section and "tsc" not in section
    assert chr(0x2014) not in out


def test_a_dry_view_of_a_forced_batch_has_no_em_dash(capsys, no_claude):
    assert chr(0x2014) not in dry(capsys, "--tasks", PY, "--forced-use", "--restrict-search")
