"""A third language is a plugin: a profile and one registry entry (ripple/registry.py).

Two made-up languages, Alpha (`.fa`) and Beta (`.fb`), are registered beside Python through the registry, each with
a stub front end that reads lines like `sym pkg.f` (a function of that id) and `calls pkg.f pkg.g` (an edge). Nothing
here needs tree-sitter: TypeScript is taken out of the registry for the tests, so the repo has exactly three
languages, in the order Python, Alpha, Beta.
"""

import sys
import types
from dataclasses import replace

import pytest

from ripple import registry
from ripple.cache import fingerprint, load_repo, profile_key
from ripple.languages import RepoError, ambiguity_warning, check_profile, merge_graphs, module_collisions, scan_repo
from ripple.model import Edge, Graph, Symbol
from ripple.profile import PYTHON
from ripple.queries import _profile_of, _same_language, callers
from ripple.registry import Language

ALPHA = replace(PYTHON, name="alpha", front_end="alpha", source_suffixes=(".fa",), excluded_suffixes=(".d.fa",), constructor_name="make_alpha")
BETA = replace(PYTHON, name="beta", front_end="beta", source_suffixes=(".fb",), excluded_suffixes=(), constructor_name="make_beta")


class BetaNotInstalled(ImportError):
    pass


def stub_front_end(name, not_installed=None, configs=None):
    """A front end module that reads `sym ID` and `calls CALLER CALLEE` lines. A file that says `needs-parser`
    raises not_installed, as a front end whose parser is missing does."""
    module = types.ModuleType(name)

    def index_files(repo, files, skipped, profile):
        symbols, edges, modules = {}, [], {}
        for path in files:
            rel = path.relative_to(repo).as_posix()
            modules[rel] = path.name.split(".")[0]
            for number, line in enumerate(path.read_text().splitlines(), 1):
                verb, *args = line.split()
                if verb == "needs-parser":
                    raise not_installed(f"{name} needs its parser")
                if verb == "broken-import":
                    raise ImportError(f"{name} has a broken import")  # an ImportError that is not a missing parser
                if verb == "sym":
                    symbols[args[0]] = Symbol(args[0], "class" if args[0].endswith(".K") else "function", rel, number, number)
                if verb == "calls":
                    edges.append(Edge(args[0], args[1], rel, number))
        return Graph(symbols=symbols, edges=edges, modules=modules, skipped_dirs=skipped)

    module.index_files = index_files
    if not_installed:
        module.NotInstalled = not_installed
    if configs:
        module.config_files = lambda repo, sources: [c for c in configs if (repo / c).is_file()]
    return module


@pytest.fixture
def three(monkeypatch):
    """The registry holds Python, Alpha and Beta, and nothing else."""
    original = dict(registry.LANGUAGES)
    monkeypatch.setitem(sys.modules, "fake_alpha", stub_front_end("fake_alpha", configs=["alpha.cfg"]))
    monkeypatch.setitem(sys.modules, "fake_beta", stub_front_end("fake_beta", not_installed=BetaNotInstalled))
    registry.LANGUAGES.clear()
    registry.LANGUAGES["python"] = original["python"]
    registry.LANGUAGES["alpha"] = Language(ALPHA, "Alpha", "fake_alpha")
    registry.LANGUAGES["beta"] = Language(BETA, "Beta", "fake_beta")
    yield registry.LANGUAGES
    registry.LANGUAGES.clear()
    registry.LANGUAGES.update(original)


CLASH = {
    "shared.py": "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
    "shared_a.fa": "sym shared.helper\nsym alpha_only.g\ncalls alpha_only.g shared.helper\nsym only_ab.x\ncalls alpha_only.g only_ab.x\n",
    "shared_b.fb": "sym shared.helper\nsym beta_only.f\ncalls beta_only.f shared.helper\nsym only_ab.x\ncalls beta_only.f only_ab.x\nsym beta_only.h\n",
}


def test_the_registry_lists_python_then_typescript_and_every_message_reads_from_it():
    assert list(registry.LANGUAGES) == ["python", "typescript"]
    assert registry.default_language().profile.name == "python"
    assert registry.join(["Python", "TypeScript"]) == "Python and TypeScript"
    assert registry.join(["a", "b", "c"], "or") == "a, b or c"


def test_a_file_is_owned_by_the_first_profile_that_reads_its_suffix_and_not_by_one_that_excludes_it(three):
    assert registry.language_of("pkg/a.py").profile is PYTHON
    assert registry.language_of("pkg/a.fa").profile is ALPHA
    assert registry.language_of("pkg/a.d.fa").profile is PYTHON  # a declaration file belongs to no one: the default reads it
    assert registry.language_of("b.fb").profile is BETA and registry.language_of("Makefile").profile is PYTHON
    assert [registry.rank(f) for f in ("a.py", "a.fa", "a.fb")] == [0, 1, 2]


def test_scan_finds_the_files_of_all_three_languages_and_names_them_in_its_errors(three, make_repo, tmp_path):
    repo = make_repo({**CLASH, "types.d.fa": "sym ignored\n"})
    scan = scan_repo(repo)
    assert [profile.name for profile in scan.profiles] == ["python", "alpha", "beta"]
    assert {name: [p.name for p in paths] for name, paths in scan.files.items()} == {
        "python": ["shared.py"], "alpha": ["shared_a.fa"], "beta": ["shared_b.fb"],
    }  # fmt: skip
    assert [p.name for p in scan_repo(repo, "beta").profiles] == ["beta"]
    with pytest.raises(RepoError, match="unknown language 'go': choose one of auto, python, alpha, beta"):
        scan_repo(repo, "go")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RepoError) as error:
        scan_repo(empty)
    assert str(error.value) == (
        f"no Python, Alpha or Beta source files in {empty} (looked for .py, .fa and .fb; "
        "node_modules, virtualenvs, build output and .d.fa files are skipped)"
    )
    with pytest.raises(RepoError, match=r"no Beta source files \(\.fb\)"):
        scan_repo(empty, "beta")


def test_check_uses_the_first_language_the_repo_has(three, make_repo):  # (make_repo writes into one directory, so the files accumulate)
    assert check_profile(make_repo({"a.fb": "sym x.y\n"})) is BETA
    assert check_profile(make_repo({"a.fa": "sym x.y\n"})) is ALPHA  # same directory: Alpha comes before Beta
    assert check_profile(make_repo({"a.py": "x = 1\n"})) is PYTHON


def test_each_language_is_indexed_by_its_own_front_end_and_cached_under_its_own_key(three, make_repo, tmp_path):
    repo = make_repo(CLASH)
    first = load_repo(repo)
    assert [(lang.name, lang.files, lang.cached) for lang in first.languages] == [("python", 1, False), ("alpha", 1, False), ("beta", 1, False)]
    assert "alpha.helper" not in first.graph.symbols and "beta_only.f" in first.graph.symbols
    again = load_repo(repo)
    assert again.cached and again.graph.to_dict() == first.graph.to_dict()
    assert profile_key(PYTHON) == "" and profile_key(ALPHA).startswith("alpha-") and profile_key(BETA).startswith("beta-")
    assert len({profile_key(ALPHA), profile_key(BETA)}) == 2


def test_a_front_end_names_the_config_files_the_cache_fingerprints(three, make_repo):
    repo = make_repo(CLASH)
    assert not any("cfg" in name for name in fingerprint(repo, ALPHA))
    (repo / "alpha.cfg").write_text("one\n")
    assert "alpha.cfg" in fingerprint(repo, ALPHA)
    assert "alpha.cfg" not in fingerprint(repo, BETA) and "alpha.cfg" not in fingerprint(repo, PYTHON)
    assert load_repo(repo).cached is False  # the new config is a changed fingerprint for Alpha only
    assert [lang.cached for lang in load_repo(repo).languages] == [True, True, True]
    (repo / "alpha.cfg").write_text("two, longer\n")
    assert [lang.cached for lang in load_repo(repo).languages] == [True, False, True]


def test_the_earliest_language_wins_an_id_and_the_later_two_drop_it_with_their_calls(three, make_repo):
    result = load_repo(make_repo(CLASH))
    graph = result.graph
    assert graph.symbols["shared.helper"].file == "shared.py"  # Python, Alpha and Beta define it: Python's stays
    assert graph.symbols["only_ab.x"].file == "shared_a.fa"  # Alpha and Beta: Alpha's stays
    assert graph.ambiguous_ids == {
        "only_ab.x": ["shared_a.fa", "shared_b.fb"],
        "shared.helper": ["shared.py", "shared_a.fa", "shared_b.fb"],
    }
    assert {(e.caller, e.callee, e.file) for e in graph.edges} == {
        ("shared.caller", "shared.helper", "shared.py"), ("alpha_only.g", "only_ab.x", "shared_a.fa"),
    }  # Alpha keeps its call into the id it won; every Beta call, and Alpha's into Python's id, is gone
    assert {"alpha_only.g", "beta_only.f", "beta_only.h"} <= set(graph.symbols)  # what is not in a clash stays
    assert graph.stats()["edges"] == 2
    assert result.warnings == [
        "Python, Alpha and Beta define 2 ids: ripple kept the Python or Alpha symbol and left out the Alpha or Beta one "
        "and the calls that name it, and does not answer questions about them: "
        "shared.helper (shared.py, shared_a.fa, shared_b.fb), only_ab.x (shared_a.fa, shared_b.fb)"
    ]
    assert result.incomplete


def test_the_order_of_the_graphs_decides_who_wins(three):
    def graph(file):
        return Graph(symbols={"m.f": Symbol("m.f", "function", file, 1, 2)})

    forward = merge_graphs([graph("a.py"), graph("a.fa"), graph("a.fb")])
    assert forward.symbols["m.f"].file == "a.py" and forward.ambiguous_ids == {"m.f": ["a.fa", "a.fb", "a.py"]}
    backward = merge_graphs([graph("a.fb"), graph("a.fa"), graph("a.py")])
    assert backward.symbols["m.f"].file == "a.fb" and backward.ambiguous_ids == {"m.f": ["a.fa", "a.fb", "a.py"]}
    middle = merge_graphs([graph("a.fa"), Graph(), graph("a.fb")])  # an id the first and third define, with one between
    assert middle.symbols["m.f"].file == "a.fa" and middle.ambiguous_ids == {"m.f": ["a.fa", "a.fb"]}
    later = merge_graphs([Graph(), graph("a.fa"), graph("a.fb")])  # the first graph does not define it: the second still beats the third
    assert later.symbols["m.f"].file == "a.fa" and later.ambiguous_ids == {"m.f": ["a.fa", "a.fb"]}


def test_the_warning_names_the_two_languages_of_a_pair_as_it_always_did(three):
    assert ambiguity_warning({"x": ["x.py", "x.fa"]}) == (
        "both languages define 1 id: ripple kept the Python symbol and left out the Alpha one "
        "and the calls that name it, and does not answer questions about them: x (x.py, x.fa)"
    )


def test_a_language_whose_parser_is_missing_is_left_out_by_name(three, make_repo):
    repo = make_repo({**CLASH, "z.fb": "needs-parser\n"})
    result = load_repo(repo)
    assert [lang.skipped for lang in result.languages] == [False, False, True]
    assert result.skipped == [
        "Beta was not indexed (2 files found): fake_beta needs its parser. These answers cover the Python and Alpha files only."
    ]
    assert "beta_only.f" not in result.graph.symbols


def test_an_import_error_that_is_not_a_missing_parser_is_not_reported_as_a_skipped_language(three, make_repo):
    repo = make_repo({**CLASH, "z.fb": "broken-import\n"})
    with pytest.raises(ImportError, match="fake_beta has a broken import"):
        load_repo(repo)


def test_a_repo_whose_only_language_has_no_parser_is_an_error(three, tmp_path):
    (tmp_path / "z.fb").write_text("needs-parser\n")
    with pytest.raises(RepoError, match=r"its 1 Beta file need Beta support\. fake_beta needs its parser"):
        load_repo(tmp_path)


def test_module_collisions_are_found_for_each_language_on_its_own(three):
    graph = Graph(
        modules={
            "p/dup.fa": "dup", "q/dup.fa": "dup", "r/dup.fb": "dup", "s/dup.fb": "dup", "dup.py": "dup", "pk/dup.py": "dup",
            "t/solo.fa": "solo", "u/solo.fb": "solo", "v/only_b.fb": "only_b", "w/only_b.fb": "only_b",
        }
    )  # fmt: skip
    assert module_collisions(graph) == {
        "dup": ["dup.py", "pk/dup.py", "p/dup.fa", "q/dup.fa", "r/dup.fb", "s/dup.fb"],
        "only_b": ["v/only_b.fb", "w/only_b.fb"],
    }  # one entry per module, its Python files then Alpha's then Beta's; `solo` is two languages, not a collision


def test_queries_read_each_symbol_by_the_profile_of_its_file(three, make_repo):
    symbols = {
        "pa.K": Symbol("pa.K", "class", "pa.fa", 1, 2), "pa.K.make_alpha": Symbol("pa.K.make_alpha", "method", "pa.fa", 2, 2),
        "pb.K": Symbol("pb.K", "class", "pb.fb", 1, 2), "pb.K.make_beta": Symbol("pb.K.make_beta", "method", "pb.fb", 2, 2),
        "pp.K": Symbol("pp.K", "class", "pp.py", 1, 2), "pp.K.make_alpha": Symbol("pp.K.make_alpha", "method", "pp.py", 2, 2),
        "use.go": Symbol("use.go", "function", "use.py", 1, 2),
    }  # fmt: skip
    graph = Graph(
        symbols=symbols,
        edges=[
            Edge("use.go", "pa.K.make_alpha", "use.py", 1),
            Edge("use.go", "pb.K.make_beta", "use.py", 2),
            Edge("use.go", "pp.K.make_alpha", "use.py", 3),
        ],
    )
    assert [_profile_of(symbols[s], None) for s in ("pa.K", "pb.K", "pp.K")] == [ALPHA, BETA, PYTHON]
    assert _profile_of(symbols["pb.K"], PYTHON) is PYTHON  # a profile asked for outright is not second-guessed
    # calling a class runs its constructor, and each language's constructor has its own name
    assert [c["symbol"] for c in callers(graph, "pa.K")["callers"]] == ["use.go"]
    assert [c["symbol"] for c in callers(graph, "pb.K")["callers"]] == ["use.go"]
    assert callers(graph, "pp.K")["callers"] == []  # Python's constructor is __init__: `make_alpha` is a method like any other
    assert _same_language("x.fa", symbols["pa.K"]) and not _same_language("x.fb", symbols["pa.K"])
    assert _same_language("x.py", symbols["pp.K"]) and not _same_language("x.fa", symbols["pp.K"])
    assert _same_language("x.d.fa", symbols["pp.K"])  # an excluded suffix is the default's, here Python's
