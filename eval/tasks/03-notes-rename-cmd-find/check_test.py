from types import SimpleNamespace

from check_helpers import files_using

from notes import commands
from notes.cli import run, run_named
from notes.store import NoteStore


def test_renamed_without_an_alias():
    assert hasattr(commands, "cmd_search")
    assert not hasattr(commands, "cmd_find")
    assert files_using("cmd_find") == []


def test_find_subcommand(tmp_path, capsys):
    run(["--root", str(tmp_path), "add", "Grocery List", "eggs"])
    run(["--root", str(tmp_path), "find", "grocery"])
    assert "grocery-list" in capsys.readouterr().out


def test_run_named_still_runs_every_command(tmp_path, capsys):
    store = NoteStore(tmp_path)
    run_named("add", store, SimpleNamespace(title="Grocery List", body="eggs"))
    run_named("list", store, SimpleNamespace())
    run_named("find", store, SimpleNamespace(needle="Grocery"))
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("saved ")
    assert sum("grocery-list" in line for line in out[1:]) == 2
