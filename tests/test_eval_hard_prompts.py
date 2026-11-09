"""--hard-prompts: the nine TypeScript tasks (20 to 28) each carry a `hard_prompt` in task.json, the same task with every
file path, list or count of sites and hint of where uses live left out. This file holds what makes one fair: the
names and the rules about what keeps its name are still said (each task lists the phrases its hidden check depends
on), and the three tasks whose hidden check demands edits in documentation still say so, in general words.

Nothing here calls a model. Tests that run a node task skip without the fetched repository (`python -m eval.fetch`).
"""

from __future__ import annotations

import json
import re
import subprocess
import types
from pathlib import Path

import pytest

import eval.run as run_mod
from eval.arms import FORCED_USE_LINE, Arms
from eval.loader import load_task, load_tasks
from eval.node_agent import node_restricted_preamble
from eval.run import build_prompt, task_prompt

TASKS = {t.id: t for t in load_tasks()}
NODE = [t for t in TASKS.values() if t.is_node]
PYTHON = [t for t in TASKS.values() if not t.is_node]
NEEDS_CACHE = "needs the pinned repositories: python -m eval.fetch"
HARD = Arms(hard_prompts=True)
ALL = Arms(forced_use=True, hard_prompts=True, restrict_search=True)

T20, T21, T22, T23, T24, T25, T26, T27, T28 = (next(t.id for t in NODE if t.id.startswith(n)) for n in "20 21 22 23 24 25 26 27 28".split())
NOT_A_PATH = [
    (r"/", "a slash"),
    (r"\.(?:tsx?|jsx?|mdx?|json|ya?ml)\b", "a file extension"),
    (r"\b(?:README|CHANGELOG|MIGRATION|website|storybook|mdx)\b", "a place uses live"),
    (r"\d", "a count or a line number"),
]

# What each hard prompt must still say, as the phrases its hidden check (check_test.ts) depends on: the old and the new
# name, every rule about what keeps its name, "update every use", the no-alias rule and the suite rule.
KEEPS = {
    T20: ["`ResultAsync.combine`", "`ResultAsync.gather`", "Update every call to it anywhere in the repository", "`Result.combine` (the synchronous one)",
          "keeps its name", "`ResultAsync.combineWithAllErrors`", "Do not keep `combine` as an alias on `ResultAsync`", "The test suite must pass when you are done."],
    T21: ["`ResultAsync.orElse`", "`ResultAsync.recoverWith`", "Update every call to it anywhere in the repository", "`Ok.orElse`, `Err.orElse` and the `orElse` of the `IResult` interface",
          "keep their name", "Do not keep `orElse` as an alias on `ResultAsync`", "The test suite must pass when you are done."],
    T22: ["`assert`", "`assertValid`", "Update every use of it anywhere in the repository: its imports and its calls", "(`assert as assertValue`) keeps that local name",
          "The method `Struct.assert` is a different function and keeps its name", "Do not keep `assert` as an alias of the function", "The test suite must pass when you are done."],
    T23: ["`create`", "`createFrom`", "Update every use of it anywhere in the repository: its imports and its calls", "(`create as createValue`) keeps that local name",
          "The method `Struct.create` is a different function and keeps its name", "the boolean `create` flag that the test fixtures export and the test harness reads",
          "Do not keep `create` as an alias of the function", "The test suite must pass when you are done."],
    T24: ["`transform` member of the `Transform` interface", "`convert`", "with or without `implements Transform`", "every call made on one",
          "the transformer classes shown in the documentation", "`TransformDescriptor.transform`", "is a different member and keeps its name", "so do the decorators",
          "Do not keep `transform` as an alias", "The test suite must pass when you are done."],
    T25: ["`registry`", "`registerAll`", "every place that imports it, every class it decorates", "the documentation examples and text that name `@registry`",
          "The file that defines the decorator keeps its name", "The `Registry` class and its modules", "the `registry` variable in the Registry tests", "keep theirs",
          "Do not keep `registry` exported as an alias", "The test suite must pass when you are done."],
    T26: ["`SubMenu`", "`MenuGroup`", "Update every use of it anywhere in the repository", "its imports, its JSX elements and its `typeof` references",
          "the tags and import lists in documentation, except the changelog and migration pages, which record history",
          "the code samples that people copy, whether they are written as code or built as strings",
          "The file that declares the component keeps its name and so do the module specifiers that point at it",
          "the titles of the tests (the strings of `describe` and `it`)", "the section names (the `title` of a story, the `order` of the sections, the label of a docs page)",
          "every other identifier that merely starts with `SubMenu`", "`SubMenuProps`", "`SubMenuFR`", "`SubMenuContent`", "`SubMenuActiveContext`", "`SubMenuExpandIcon`",
          "the wording of the messages the component logs at run time (the text passed to `console.warn`), which the suite asserts and which keeps its text",
          "Do not keep `SubMenu` as an alias", "The test suite must pass when you are done."],
    T27: ["`useMediaQuery`", "`useMatchMedia`", "Update every use of it anywhere in the repository, including any use that names the export only as a string",
          "The file that defines the hook keeps its name and so do the module specifiers that point at it", "so does the title of the hook's `describe` block",
          "Do not keep `useMediaQuery` as an alias", "The test suite must pass when you are done."],
    T28: ["`transitionDuration` prop of the `Menu` component", "`MenuContextProps.transitionDuration`", "`slideDuration`", "anywhere in the repository",
          "the documentation of the Menu prop", "values that are built by hand with no type and carry it under that name",
          "The `transitionDuration` of `Sidebar`, of the sidebar context, of the styled wrappers and of the playground state is a different property and keeps its name",
          "the story's name `transitionDuration`", "the changelogs, which record history", "Do not keep `transitionDuration` as an alias on `Menu`",
          "The test suite must pass when you are done."],
}
# The hidden checks that demand an edit nothing runs: the documentation. A hard prompt still says so, in general words.
DOCS_DEMANDED = {T24: "documentation", T25: "documentation", T26: "documentation", T28: "documentation"}


def fetched(*ids):
    return [pytest.param(i, id=i, marks=pytest.mark.skipif(not TASKS[i].available, reason=NEEDS_CACHE)) for i in ids]


# ------------------------------------------------------------------------------------------------ the prompts


def test_the_nine_node_tasks_have_a_hard_prompt_and_no_python_task_does():
    assert [t.id for t in NODE] == list(KEEPS) and len(NODE) == 9
    assert all(t.hard_prompt and t.hard_prompt != t.prompt for t in NODE)
    assert all(t.hard_prompt is None for t in PYTHON)


@pytest.mark.parametrize("task", NODE, ids=[t.id for t in NODE])
def test_a_hard_prompt_names_no_path_no_count_and_no_place_uses_live(task):
    for pattern, what in NOT_A_PATH:
        found = re.search(pattern, task.hard_prompt)
        assert not found, f"{task.id}: the hard prompt contains {what}: {found.group(0)!r}"
    assert len(task.hard_prompt) < len(task.prompt) and chr(0x2014) not in task.hard_prompt


@pytest.mark.parametrize("task", NODE, ids=[t.id for t in NODE])
def test_a_hard_prompt_still_says_every_phrase_its_hidden_check_depends_on(task):
    missing = [p for p in KEEPS[task.id] if p not in task.hard_prompt]
    assert not missing, f"{task.id}: the hard prompt no longer says {missing}"


@pytest.mark.parametrize("task_id", sorted(DOCS_DEMANDED))
def test_a_task_whose_hidden_check_reads_the_documentation_still_tells_the_agent_to_edit_it(task_id):
    assert DOCS_DEMANDED[task_id] in TASKS[task_id].hard_prompt


def test_the_hard_prompts_keep_the_rule_the_normal_ones_carry_that_a_check_depends_on():
    """Task 26's warning text: a rule the normal prompt states and a correct-looking solution breaks must survive."""
    assert "console.warn" in TASKS[T26].hard_prompt and "keeps its text" in TASKS[T26].hard_prompt
    for task in NODE:
        assert "alias" in task.hard_prompt and "The test suite must pass" in task.hard_prompt


def test_the_normal_prompts_are_untouched():
    assert TASKS[T20].prompt.startswith("Rename the static method `ResultAsync.combine` (in src/result-async.ts)")
    assert all(t.prompt.strip() for t in TASKS.values())


# ------------------------------------------------------------------------------------------- the task field


def test_the_hard_prompt_field_is_optional_and_must_be_text_when_given(tmp_path):
    base = {"id": "t1", "fixture": "bench/fixtures/shop", "prompt": "p", "min_tests": 1, "why": "w"}
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "check_test.py").write_text("")
    for extra, ok in (({}, None), ({"hard_prompt": "harder"}, "harder"), ({"hard_prompt": ""}, ValueError), ({"hard_prompt": 3}, ValueError)):
        (tmp_path / "t1" / "task.json").write_text(json.dumps({**base, **extra}))
        if ok is ValueError:
            with pytest.raises(ValueError, match="hard_prompt"):
                load_task(tmp_path / "t1")
        else:
            assert load_task(tmp_path / "t1").hard_prompt == ok


# ---------------------------------------------------------------------------------------------- choosing it


def test_task_prompt_is_the_hard_one_only_under_the_flag_and_only_for_a_task_that_has_one():
    node, python = TASKS[T20], PYTHON[0]
    assert task_prompt(node, None) == node.prompt == task_prompt(node, Arms()) == task_prompt(node, Arms(forced_use=True, restrict_search=True))
    assert task_prompt(node, HARD) == node.hard_prompt
    assert task_prompt(python, HARD) == python.prompt  # no hard prompt: its normal one


def test_the_arm_label_names_hard_and_orders_the_flags():
    assert HARD.label == "hard" and Arms(hard_prompts=True, restrict_search=True).label == "hard+restricted"
    assert Arms(forced_use=True, hard_prompts=True).label == "forced+hard" and ALL.label == "forced+hard+restricted"


class Fake:
    cmds: list = []

    def __init__(self, cmd, cwd, env, stdin, stdout, stderr, start_new_session):
        Fake.cmds.append(cmd)
        self.returncode, self.pid = 0, 0

    def wait(self, timeout=None):
        return 0


@pytest.mark.parametrize("task_id", fetched(T20, T26))
def test_run_one_gives_the_agent_the_hard_prompt_in_both_arms_and_records_the_arm(monkeypatch, tmp_path, task_id):
    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": Fake}))
    monkeypatch.setattr(run_mod, "check_task", lambda task, repo: {"success": False, "failures": []})
    task = TASKS[task_id]
    for mode in ("with-ripple", "without"):
        Fake.cmds = []
        root = tmp_path / mode
        root.mkdir()
        record = run_mod.run_one(task, mode, 1, root, root / "bin", types.SimpleNamespace(model="sonnet", timeout=5, ripple_python=None, arms=ALL))
        assert Fake.cmds[0][2] == build_prompt(task.hard_prompt, mode, task, ALL)
        assert Fake.cmds[0][2].endswith(task.hard_prompt) and task.prompt not in Fake.cmds[0][2]
        assert record["arm"] == "forced+hard+restricted"
        assert (FORCED_USE_LINE in Fake.cmds[0][2]) == (mode == "with-ripple")
        assert node_restricted_preamble(task.spec) in Fake.cmds[0][2]


# -------------------------------------------------------------------------------------------- main(): CLI


@pytest.fixture
def no_claude(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError(f"started something it should not have: {a}")

    monkeypatch.setattr(run_mod, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "run": refuse, "Popen": refuse}))
    monkeypatch.setattr(run_mod, "run_one", refuse)
    monkeypatch.setattr(run_mod, "ripple_problems", lambda *a, **k: [])


def refused(capsys, *argv):
    with pytest.raises(SystemExit) as exit_:
        run_mod.main(list(argv))
    assert exit_.value.code == 2
    return capsys.readouterr().err


def test_hard_prompts_with_a_python_task_is_refused_before_anything_starts(capsys, no_claude):
    err = refused(capsys, "--dry", "--tasks", PYTHON[0].id, "--hard-prompts")
    assert "--hard-prompts" in err and "20 to 28 only" in err and PYTHON[0].id in err


def test_hard_prompts_on_the_default_python_selection_is_refused_too_even_with_no_repository_fetched(capsys, no_claude, monkeypatch, tmp_path):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "empty"))
    err = refused(capsys, "--hard-prompts", "--out", str(tmp_path / "x.json"))
    assert "20 to 28 only" in err and "not fetched" not in err


@pytest.mark.parametrize("task_id", fetched(T20))
def test_a_mixed_selection_with_one_python_task_is_refused(capsys, no_claude, task_id):
    err = refused(capsys, "--dry", "--tasks", f"{PYTHON[0].id},{task_id}", "--hard-prompts")
    assert PYTHON[0].id in err and task_id not in err


def dry(capsys, *argv):
    assert run_mod.main(["--dry", *argv]) == 0
    return capsys.readouterr().out


@pytest.mark.skipif(not all(t.available for t in NODE), reason=NEEDS_CACHE)
def test_the_hard_dry_view_says_how_many_tasks_have_a_hard_prompt(capsys, no_claude):
    out = dry(capsys, "--runtime", "node", "--hard-prompts", "--ripple-python", "/py/bin/python")
    assert "arm: hard" in out and "hard prompts: 9 of 9 selected tasks have one" in out
    assert "tasks (9):" in out and "runs: 9 tasks x 2 modes x 3 repeats = 54" in out


@pytest.mark.skipif(not all(t.available for t in NODE), reason=NEEDS_CACHE)
def test_the_all_flags_dry_view_on_node_shows_every_part_of_the_arm(capsys, no_claude):
    out = dry(capsys, "--runtime", "node", "--forced-use", "--hard-prompts", "--restrict-search", "--ripple-python", "/py/bin/python")
    assert "arm: forced+hard+restricted" in out and "hard prompts: 9 of 9" in out
    assert out.count(FORCED_USE_LINE) == 2  # the with-ripple preamble of the vitest and of the jest tasks
    assert out.count("[node, vitest] disallowed tools, ") == 2 and "Bash(tsc:*)" not in out.split("allowed tools")[1]


def test_a_batch_without_the_flag_does_not_print_the_hard_line(capsys, no_claude):
    assert "hard prompts" not in dry(capsys, "--tasks", PYTHON[0].id)


def meta_of(monkeypatch, argv):
    seen = {}

    class Stop(Exception):
        pass

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


@pytest.mark.parametrize("task_id", fetched(T20))
def test_a_hard_batch_records_the_flag_and_which_tasks_ran_a_hard_prompt(monkeypatch, tmp_path, task_id):
    meta = meta_of(monkeypatch, ["--tasks", task_id, "--repeats", "1", "--hard-prompts", "--out", str(tmp_path / "x.json")])
    assert meta["arm"] == "hard" and meta["arm_flags"] == {"forced_use": False, "hard_prompts": True, "restrict_search": False}
    assert meta["hard_prompt_tasks"] == [task_id]
