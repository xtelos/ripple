"""A file no `mod` declaration reaches was not read: it is not a file that could not be parsed, and the header says so.

The count after "could not be parsed" is parse failures only; what no `mod` reaches (and a `mod x;` that names no file)
has a count and a note of its own. A repo with no such file prints what it printed before.
"""

import pytest

pytest.importorskip("tree_sitter_rust")

from ripple import cli  # noqa: E402
from ripple.cache import load_repo  # noqa: E402

CARGO = '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n'


def header(repo, files):
    for rel, source in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(source)
    return cli.format_index(str(repo), load_repo(repo, cache_dir=repo / ".cache"))


def test_unreached_files_are_counted_as_not_read_and_a_parse_failure_as_one_that_could_not_be_parsed(tmp_path):
    text = header(
        tmp_path,
        {
            "app.py": "def f():\n    pass\n",
            "Cargo.toml": CARGO,
            "src/lib.rs": "mod missing;\nmod real;\npub fn ok() {}\npub fn bad( {\n",
            "src/real.rs": "pub fn f() {}\n",
            "src/stray.rs": "pub fn s() {}\n",
        },
    )
    assert "Rust: 3 files (1 could not be parsed, 2 not read)" in text
    assert "could not parse (Rust, 1 file):\n  src/lib.rs\n" in text
    assert "note: not read, the reason after each (Rust, 2 items):" in text
    assert "  src/stray.rs (no `mod` declaration reaches it from a crate root, so it is not read)" in text
    assert "`mod missing;` names no file" in text


def test_a_repo_with_nothing_unread_prints_no_note_and_no_count(tmp_path):
    text = header(tmp_path, {"app.py": "def f():\n    pass\n", "Cargo.toml": CARGO, "src/lib.rs": "pub fn ok() {}\n"})
    assert "not read" not in text and "Rust: 1 file\n" in text + "\n"


def test_a_rust_only_repo_still_notes_what_was_not_read(tmp_path):
    text = header(tmp_path, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn ok() {}\n", "src/stray.rs": "pub fn s() {}\n"})
    assert "note: not read, the reason after each (Rust, 1 item):" in text
    assert "could not be parsed" not in text
