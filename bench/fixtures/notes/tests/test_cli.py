import runpy
import sys
from types import SimpleNamespace

import pytest

from notes.cli import run, run_named
from notes.helpers import slugify
from notes.store import NoteStore


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"


def test_add_then_list(tmp_path, capsys):
    assert run(["--root", str(tmp_path), "add", "My Note", "body"]) == 0
    run(["--root", str(tmp_path), "list"])
    run(["--root", str(tmp_path), "find", "my"])
    assert "my-note" in capsys.readouterr().out


def test_find_by_name(tmp_path, capsys):
    store = NoteStore(tmp_path)
    store.add("Grocery List", "eggs")
    run_named("add", store, SimpleNamespace(title="Other", body="x"))
    run_named("find", store, SimpleNamespace(needle="Grocery"))
    assert "grocery-list" in capsys.readouterr().out


def test_module_entry_point(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["notes", "--root", str(tmp_path), "list"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("notes", run_name="__main__")
    assert exit_info.value.code == 0
