import os

from ripple.cache import RepoIndex, load_graph


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


def test_repo_index_reuses_the_in_memory_graph(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": "def f():\n    pass\n"})
    index = RepoIndex(repo, cache_dir=tmp_path_factory.mktemp("cache"))
    first = index.graph()
    assert index.graph() is first
