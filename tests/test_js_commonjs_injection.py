"""A block comment between two tokens changes nothing ripple says about CommonJS.

The same invariance test as test_ts_comment_injection.py (a `/* c */` before a seeded share of the token boundaries of every source
file of a repo, the graph of the copy must be the graph of the original), over the CommonJS probes in tests/fixtures/js_probes/cjs:
`require /* c */ ( /* c */ './a' /* c */ )`, `module /* c */ . /* c */ exports`, `exports . x /* c */ = f`, `/* c */ this`, a
destructuring pattern with comments in it, and every other shape of those probes. It needs tree-sitter and no node.
"""

import shutil
from pathlib import Path

import pytest
from test_ts_comment_injection import commented, difference, plan, snapshot, variants

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

PROBES = Path(__file__).resolve().parent / "fixtures" / "js_probes" / "cjs"
REPOS = {p.name: p for p in sorted(PROBES.iterdir()) if p.is_dir()}


def write_variant(repo: Path, target: Path, chosen: dict[str, list[int]]) -> None:
    """test_ts_comment_injection.write_variant, but a symbolic link stays one (a probe may hold links) and is not written through."""
    shutil.copytree(repo, target, symlinks=True, ignore=shutil.ignore_patterns("expected.json", "__pycache__"))
    for file, points in chosen.items():
        path = target / file
        if not path.is_symlink():
            path.write_bytes(commented(path.read_bytes(), points))


@pytest.mark.parametrize("key", list(REPOS))
def test_a_comment_between_tokens_changes_nothing_ripple_says_about_commonjs(key, tmp_path):
    repo = REPOS[key]
    original = snapshot(repo, tmp_path / "cache-original")
    for number, chosen in enumerate(variants(repo, f"cjs:{key}")):
        if not chosen:
            continue
        target = tmp_path / f"variant-{number}"
        write_variant(repo, target, chosen)
        wrong = difference(original, snapshot(target, tmp_path / f"cache-{number}"))
        assert not wrong, f"{key} variant {number}: {len(wrong)} differences\n" + "\n".join(wrong)


def test_the_injection_covers_many_boundaries_of_commonjs_files():
    total = sum(len(points) for repo in REPOS.values() for points in plan(repo).values())
    assert total >= 3000, total
