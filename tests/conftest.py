import textwrap
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "bench" / "fixtures"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path_factory, monkeypatch):
    """Keep the test suite from writing into the real ~/.cache/ripple."""
    monkeypatch.setenv("RIPPLE_CACHE_DIR", str(tmp_path_factory.mktemp("ripple-cache")))


@pytest.fixture
def make_repo(tmp_path):
    """Write a dict of {relative path: source} into a temp dir and return it."""

    def _make(files: dict) -> Path:
        for rel, source in files.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source))
        return tmp_path

    return _make


@pytest.fixture
def shop_repo():
    return FIXTURES / "shop"


def callers_of(graph, symbol_id):
    return {e.caller for e in graph.edges if e.callee == symbol_id}


def callees_of(graph, symbol_id):
    return {e.callee for e in graph.edges if e.caller == symbol_id}
