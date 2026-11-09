import json
import os

from ripple import __version__
from ripple.cache import RepoIndex, cache_file, load_graph


def test_second_load_comes_from_cache(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    _, cached = load_graph(repo, cache_dir)
    assert cached is False
    graph, cached = load_graph(repo, cache_dir)
    assert cached is True
    assert "m.f" in graph.symbols


def test_edit_invalidates_the_cache(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    source = repo / "m.py"
    source.write_text("def f():\n    pass\n\ndef g():\n    f()\n")
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False
    assert "m.g" in graph.symbols


def test_new_and_deleted_files_invalidate_the_cache(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    (repo / "n.py").write_text("def g():\n    pass\n")
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False and "n.g" in graph.symbols
    (repo / "n.py").unlink()
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False and "n.g" not in graph.symbols


def test_cache_written_by_an_older_format_is_rebuilt(make_repo, tmp_path_factory):
    """The format before call-result, annotation, wrapper, partial and table
    resolution: its graph lacks edges and fields the current code relies on."""
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    path = cache_file(repo.resolve(), cache_dir)
    saved = json.loads(path.read_text())
    saved["format"] = f"{__version__}-4"
    saved["graph"]["symbols"] = {}
    path.write_text(json.dumps(saved))
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False
    assert "m.f" in graph.symbols


def test_cache_written_before_edges_carried_their_decoration_is_rebuilt(make_repo, tmp_path_factory):
    """Format 6: wrapped edges and calls through a wrapper have no via, so impact and path would
    chain through a shared wrapper, and callers would list no line that names a wrapped function."""
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    path = cache_file(repo.resolve(), cache_dir)
    saved = json.loads(path.read_text())
    saved["format"] = f"{__version__}-6"
    saved["graph"]["symbols"] = []
    path.write_text(json.dumps(saved))
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False
    assert "m.f" in graph.symbols


def test_cache_written_before_store_tokens_decided_tables_and_partials_is_rebuilt(make_repo, tmp_path_factory):
    """Format 7: a table or partial was followed unless a store was found on that module, so a graph from
    then holds edges the token rule refuses and keeps the edges of a wrapper whose result it mistyped."""
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    path = cache_file(repo.resolve(), cache_dir)
    saved = json.loads(path.read_text())
    saved["format"] = f"{__version__}-7"
    saved["graph"]["symbols"] = []
    path.write_text(json.dumps(saved))
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False
    assert "m.f" in graph.symbols


def test_cache_written_before_decorations_and_skipped_class_bodies_were_recorded_is_rebuilt(make_repo, tmp_path_factory):
    """Format 8: a TypeScript graph has no decorations and no flagged class-expression calls, so a question about a
    decorator would answer from it as if the symbol were never applied. It is rebuilt, never reused."""
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    path = cache_file(repo.resolve(), cache_dir)
    saved = json.loads(path.read_text())
    assert saved["format"] != f"{__version__}-8"
    saved["format"] = f"{__version__}-8"
    saved["graph"]["symbols"] = []
    path.write_text(json.dumps(saved))
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False
    assert "m.f" in graph.symbols


def test_repo_index_reuses_the_in_memory_graph(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    index = RepoIndex(repo, cache_dir=tmp_path_factory.mktemp("cache"))
    first = index.graph()
    assert index.graph() is first
