"""Real repositories contain files ripple was not written for: broken symlinks,
unreadable files, odd encodings, generated code nested a thousand levels deep.
One such file must cost that file, never the whole index."""

import os
import subprocess
import sys

import pytest
from conftest import callers_of

from ripple.cache import fingerprint, load_graph
from ripple.indexer import build_graph

GOOD = "def target():\n    return 1\n\ndef user():\n    return target()\n"


def test_broken_symlink_does_not_crash_the_index(make_repo):
    repo = make_repo({"good.py": GOOD})
    os.symlink(repo / "missing.py", repo / "broken.py")
    os.symlink(repo / "self.py", repo / "self.py")  # a loop: resolving it raises ELOOP
    assert "broken.py" not in fingerprint(repo)
    graph, _ = load_graph(repo)
    assert callers_of(graph, "good.target") == {"good.user"}
    assert graph.parse_errors == []


def test_symlinked_directory_loop_is_not_followed(make_repo):
    repo = make_repo({"pkg/good.py": GOOD})
    os.symlink(repo, repo / "pkg" / "loop", target_is_directory=True)
    graph, _ = load_graph(repo)
    assert set(graph.modules) == {"pkg/good.py"}


def test_symlink_to_a_file_is_still_indexed(make_repo):
    repo = make_repo({"good.py": GOOD})
    os.symlink(repo / "good.py", repo / "alias.py")
    graph, _ = load_graph(repo)
    assert "alias.py" in graph.modules


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_named_pipe_called_x_py_is_skipped_not_read(make_repo):
    repo = make_repo({"good.py": GOOD})
    os.mkfifo(repo / "pipe.py")  # opening it for reading would block forever
    done = subprocess.run(
        [sys.executable, "-m", "ripple", "index", str(repo)], capture_output=True, text=True, timeout=15
    )
    assert done.returncode == 0, done.stderr
    assert "1 files" in done.stdout


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="root reads anything")
def test_unreadable_file_is_reported_not_fatal(make_repo):
    repo = make_repo({"good.py": GOOD, "secret.py": "def f():\n    pass\n"})
    os.chmod(repo / "secret.py", 0)
    try:
        graph = build_graph(repo)
    finally:
        os.chmod(repo / "secret.py", 0o644)
    assert graph.parse_errors == ["secret.py"]
    assert callers_of(graph, "good.target") == {"good.user"}


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="root enters anything")
def test_a_directory_that_can_be_listed_but_not_entered_is_skipped_and_reported(make_repo):
    repo = make_repo({"good.py": GOOD, "locked/inside.py": "def f():\n    pass\n"})
    os.chmod(repo / "locked", 0o644)  # readable, not searchable: stat of anything inside fails
    try:
        graph = build_graph(repo)
    finally:
        os.chmod(repo / "locked", 0o755)
    assert graph.skipped_dirs == ["locked"]
    assert callers_of(graph, "good.target") == {"good.user"}


def test_a_file_whose_type_cannot_be_determined_is_left_to_the_read(make_repo, monkeypatch):
    repo = make_repo({"good.py": GOOD, "odd.py": "def f():\n    pass\n"})
    real = type(repo).is_file

    def is_file(self):
        if self.name == "odd.py":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(type(repo), "is_file", is_file)
    graph = build_graph(repo)
    # not skipped silently: reading it decides (here it reads fine; an unreadable one is a parse error)
    assert "odd.py" in graph.modules
    assert callers_of(graph, "good.target") == {"good.user"}


def test_utf8_bom_is_not_a_parse_error(make_repo):
    repo = make_repo({"good.py": GOOD})
    (repo / "bom.py").write_bytes(b"\xef\xbb\xbffrom good import target\n\ndef f():\n    return target()\n")
    graph = build_graph(repo)
    assert graph.parse_errors == []
    assert callers_of(graph, "good.target") == {"good.user", "bom.f"}


def test_coding_cookie_is_honoured(make_repo):
    repo = make_repo({"good.py": GOOD})
    (repo / "old.py").write_bytes(
        b"# -*- coding: latin-1 -*-\nfrom good import target\n\ndef f():\n    s = '\xe9'\n    return target()\n"
    )
    graph = build_graph(repo)
    assert graph.parse_errors == []
    assert callers_of(graph, "good.target") == {"good.user", "old.f"}


def test_bytes_that_are_not_python_cost_only_their_file(make_repo):
    repo = make_repo({"good.py": GOOD})
    (repo / "badutf8.py").write_bytes(b"def f():\n    return '\xff\xfe'\n")
    (repo / "nul.py").write_bytes(b"def f():\n    return 1\n\x00\n")
    graph = build_graph(repo)
    assert sorted(graph.parse_errors) == ["badutf8.py", "nul.py"]
    assert callers_of(graph, "good.target") == {"good.user"}
