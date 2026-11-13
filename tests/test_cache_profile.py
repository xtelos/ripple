"""The cache and the language profile.

A saved graph is only valid for the profile that resolved it, so the profile is part of what
the cache is keyed on. For the default PYTHON profile the file name, the bytes and the format
string are exactly what they were before profiles existed, so caches already on disk still load.
"""

import hashlib
import json
from dataclasses import replace

from ripple import __version__
from ripple.cache import RepoIndex, cache_file, fingerprint, load_graph
from ripple.indexer import build_graph
from ripple.profile import PYTHON

FOO = "class Foo:\n    def __init__(self): pass\n    def build(self): pass\n\ndef run():\n    Foo()\n"


def constructor_callee(graph):
    return [e.callee for e in graph.edges if e.kind == "constructor"]


def test_a_graph_built_for_one_profile_is_not_served_to_another_that_resolves_differently(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": FOO})
    cache_dir = tmp_path_factory.mktemp("cache")
    renamed = replace(PYTHON, constructor_name="build")  # still named "python", one field different
    python, cached = load_graph(repo, cache_dir)
    assert not cached and constructor_callee(python) == ["m.Foo.__init__"]
    other, cached = load_graph(repo, cache_dir, profile=renamed)
    assert not cached and constructor_callee(other) == ["m.Foo.build"]
    # and each is then served from its own record
    graph, cached = load_graph(repo, cache_dir)
    assert cached and constructor_callee(graph) == ["m.Foo.__init__"]
    graph, cached = load_graph(repo, cache_dir, profile=renamed)
    assert cached and constructor_callee(graph) == ["m.Foo.build"]


def test_two_profiles_that_share_a_name_and_differ_in_a_field_do_not_share_a_cache(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": FOO})
    cache_dir = tmp_path_factory.mktemp("cache")
    build = replace(PYTHON, constructor_name="build")
    none = replace(PYTHON, constructor_name="no_such_member")
    assert build.name == none.name == PYTHON.name
    assert cache_file(repo.resolve(), cache_dir, build) != cache_file(repo.resolve(), cache_dir, none)
    assert constructor_callee(load_graph(repo, cache_dir, profile=build)[0]) == ["m.Foo.build"]
    graph, cached = load_graph(repo, cache_dir, profile=none)
    assert not cached and constructor_callee(graph) == ["m.Foo"]
    graph, cached = load_graph(repo, cache_dir, profile=build)
    assert cached and constructor_callee(graph) == ["m.Foo.build"]


def test_the_cache_for_the_python_profile_is_exactly_what_it_was(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": FOO})
    cache_dir = tmp_path_factory.mktemp("cache")
    graph, _ = load_graph(repo, cache_dir)
    key = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]
    [written] = list(cache_dir.iterdir())
    assert written.name == f"{repo.name}-{key}.json"
    assert cache_file(repo.resolve(), cache_dir) == written
    expected = {"format": f"{__version__}-28", "files": fingerprint(repo), "graph": graph.to_dict()}
    assert written.read_text() == json.dumps(expected)
    # a profile with the same values is the same profile: same file, served from it
    assert load_graph(repo, cache_dir, profile=replace(PYTHON))[1] is True
    assert list(cache_dir.iterdir()) == [written]


def test_a_cache_written_before_profiles_existed_still_loads(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": FOO})
    cache_dir = tmp_path_factory.mktemp("cache")
    old = {"format": f"{__version__}-28", "files": fingerprint(repo), "graph": build_graph(repo).to_dict()}
    cache_file(repo.resolve(), cache_dir).write_text(json.dumps(old))
    graph, cached = load_graph(repo, cache_dir)
    assert cached is True and "m.Foo" in graph.symbols


def test_a_profile_with_another_name_gets_its_own_file_and_a_clean_miss(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": FOO})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    [python_file] = list(cache_dir.iterdir())
    ruby = replace(PYTHON, name="ruby")
    assert cache_file(repo.resolve(), cache_dir, ruby) != python_file
    assert "ruby" in cache_file(repo.resolve(), cache_dir, ruby).name
    _, cached = load_graph(repo, cache_dir, profile=ruby)
    assert cached is False
    assert len(list(cache_dir.iterdir())) == 2 and python_file.exists()
    assert load_graph(repo, cache_dir, profile=ruby)[1] is True
    assert load_graph(repo, cache_dir)[1] is True  # the other language's run did not touch it


def test_a_record_that_names_another_profile_is_a_miss_not_an_error(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": FOO})
    cache_dir = tmp_path_factory.mktemp("cache")
    load_graph(repo, cache_dir)
    path = cache_file(repo.resolve(), cache_dir)
    record = json.loads(path.read_text())
    record["profile"] = "some-other-profile"
    path.write_text(json.dumps(record))
    graph, cached = load_graph(repo, cache_dir)
    assert cached is False and "m.Foo" in graph.symbols
    assert "profile" not in json.loads(path.read_text())  # rebuilt, and written as a Python cache is


def test_a_repo_index_holds_the_graph_of_its_own_profile(make_repo, tmp_path_factory):
    repo = make_repo({"m.py": FOO})
    cache_dir = tmp_path_factory.mktemp("cache")
    RepoIndex(repo, cache_dir).graph()
    index = RepoIndex(repo, cache_dir, profile=replace(PYTHON, constructor_name="build"))
    assert constructor_callee(index.graph()) == ["m.Foo.build"]
