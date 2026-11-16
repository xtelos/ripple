"""The CommonJS model's edges that a probe repo cannot show: files that cannot be read, how a specifier is read, a package.json `type` as
node 24 reads it, and a cache that does not go stale when a file a require names appears.

The probes in tests/fixtures/js_probes/cjs (test_js_commonjs_probes.py) hold the shapes node itself ran. These need the file system
(a mode, a file added after a first index), or a ModuleMap with no repo behind it.
"""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cache import load_repo  # noqa: E402
from ripple.ts_modules import ModuleMap  # noqa: E402

A = "function helper() { return 'lib/a:helper'; }\nmodule.exports = { helper };\n"
PATCH = "require('../lib/a').helper = function () { return 'patched'; };\n"
MAIN = "const a = require('./lib/a');\nrequire('./%s');\nexports.s1 = function () { return a.helper(); };\n"


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def call_of_helper(root: Path, cache: Path) -> tuple[list[str], list[str]]:
    """(the callees ripple draws for a.helper() in main.js, the reasons it leaves that call unresolved)."""
    graph = load_repo(root, cache_dir=cache, lang="auto").graph
    edges = sorted({e.callee for e in graph.edges if e.file == "main.js" and e.line == 3 and e.kind == "call"})
    return edges, [u.reason for u in graph.unresolved if u.file == "main.js" and u.line == 3]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads a file whatever its mode")
def test_a_javascript_file_ripple_cannot_read_opens_every_commonjs_export(tmp_path):
    """What a file ripple has no text of does to the exports of any module is not known (the survivor of the M8 mutation)."""
    root = write(tmp_path / "repo", {"lib/a.js": A, "lib/patch.js": PATCH.replace("../lib/a", "./a"), "main.js": MAIN % "lib/patch"})
    edges, why = call_of_helper(root, tmp_path / "before")
    assert edges == [] and any("another file" in r for r in why), (edges, why)  # readable: the write through it is seen
    (root / "lib/patch.js").chmod(0)
    try:
        edges, why = call_of_helper(root, tmp_path / "after")
    finally:
        (root / "lib/patch.js").chmod(0o644)
    assert edges == [] and any("could not be read" in r for r in why), (edges, why)


def test_a_directory_called_dot_dot_x_is_inside_the_repo_and_an_absolute_path_is_unresolved(tmp_path):
    root = write(tmp_path / "repo", {"main.js": "", "main.ts": "", "..x/a.js": A})
    modules = ModuleMap(root, [root / "main.js", root / "main.ts"])
    assert modules.resolve("./..x/nothing", "main.js") == ("missing", "./..x/nothing")  # was "package": the base started with ".."
    assert modules.resolve("../outside", "main.js") == ("package", "../outside")
    assert modules.resolve("/abs/x", "main.js")[0] == "unresolved"
    assert modules.resolve_require("/abs/x", "main.js")[0] == "unresolved"
    assert modules.resolve_require("./..x/nothing", "main.js")[0] == "missing"
    assert modules.resolve("/abs/x", "main.ts") == ("package", "/abs/x")  # TypeScript is unchanged (tests/test_ts_modules.py)


@pytest.mark.parametrize(
    ("type_", "kind"),
    [('"module"', "module"), ('"commonjs"', "commonjs"), ('"CommonJS"', ""), ('"Module"', ""), ('""', ""), ('"x"', ""), ("5", "?"), ("null", "?"), ("true", "?"), ("[]", "?"), ("{}", "?")],
)
def test_package_type_is_what_node_24_reads(tmp_path, type_, kind):
    """A string `type` other than module or commonjs is none, and any other value makes node throw (checked with node v24.0.1)."""
    root = write(tmp_path / "repo", {"package.json": f'{{"type": {type_}}}', "x.js": ""})
    assert ModuleMap(root, [root / "x.js"]).package_type("x.js") == kind
    assert ModuleMap(root, [root / "x.js"]).node_format("x.js") == {"module": "esm", "commonjs": "cjs", "": "detect", "?": "?"}[kind]


def test_no_package_json_and_a_package_json_with_no_type_are_none(tmp_path):
    root = write(tmp_path / "repo", {"x.js": "", "p/package.json": json.dumps({"name": "p"}), "p/y.js": ""})
    modules = ModuleMap(root, [root / "x.js", root / "p/y.js"])
    assert modules.package_type("x.js") == "" and modules.package_type("p/y.js") == ""


def test_a_file_a_require_names_that_appears_later_is_not_served_from_the_cache(tmp_path):
    """The hidden patch file is not source, so only its name is in the fingerprint: adding it must change the answer."""
    root = write(tmp_path / "repo", {"lib/a.js": A, "main.js": MAIN % ".hidden/patch"})
    cache = tmp_path / "cache"
    edges, why = call_of_helper(root, cache)
    assert edges == ["lib/a:helper"] and not why, (edges, why)  # names no file: no write, so the exports are closed
    write(root, {".hidden/patch.js": PATCH})
    edges, why = call_of_helper(root, cache)
    assert edges == [] and any("does not read" in r for r in why), (edges, why)
    (root / ".hidden/patch.js").unlink()
    edges, why = call_of_helper(root, cache)
    assert edges == ["lib/a:helper"], (edges, why)


def test_a_package_committed_into_node_modules_later_is_not_served_from_the_cache(tmp_path):
    root = write(tmp_path / "repo", {"lib/a.js": A, "main.js": "const a = require('./lib/a');\nrequire('pkg');\nexports.s1 = function () { return a.helper(); };\n"})
    cache = tmp_path / "cache"
    edges, why = call_of_helper(root, cache)
    assert edges == ["lib/a:helper"], (edges, why)  # a package that is not there is outside the repo
    write(root, {"node_modules/pkg/index.js": "require('../../lib/a').helper = function () { return 'patched'; };\n"})
    edges, why = call_of_helper(root, cache)
    assert edges == [] and any("committed into the repo" in r for r in why), (edges, why)


def test_a_symbolic_link_to_a_file_appearing_later_is_not_served_from_the_cache(tmp_path):
    root = write(tmp_path / "repo", {"lib/a.js": A, "lib/patch.js": PATCH.replace("../lib/a", "./b"), "main.js": MAIN % "lib/patch"})
    cache = tmp_path / "cache"
    edges, why = call_of_helper(root, cache)
    assert edges == ["lib/a:helper"], (edges, why)  # ./b names no file yet, so it writes nothing
    (root / "lib/b.js").symlink_to("a.js")
    edges, why = call_of_helper(root, cache)
    assert edges == [] and any("symbolic link" in r for r in why), (edges, why)
