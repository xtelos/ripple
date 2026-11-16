"""CommonJS in JavaScript repos: a hand-written truth for a small repo, Node's resolution order, ES imports of CommonJS, what the
other front end's files and test frameworks do to exports, the cache, and that TypeScript does not move.

The probes against node itself are in test_js_commonjs_probes.py; these are the cases that need no node, or that no node run can
state (a mock call, a .ts file beside, a file appearing between two runs).
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.cache import load_repo  # noqa: E402
from ripple.indexer import find_source_files  # noqa: E402
from ripple.profile import JAVASCRIPT  # noqa: E402
from ripple.ts_modules import ModuleMap  # noqa: E402

A = "function helper() { return 1; }\nfunction other() { return 2; }\n"


def make(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def graph_of(root: Path, cache: Path, lang: str = "auto"):
    return load_repo(root, cache_dir=cache, lang=lang).graph


def callees(graph, file: str, line: int) -> list[str]:
    return sorted(e.callee for e in graph.edges if e.file == file and e.line == line and e.kind in ("call", "constructor"))


def reasons(graph, file: str, line: int) -> list[str]:
    return [u.reason for u in graph.unresolved if u.file == file and u.line == line]


def test_a_repo_with_a_hand_written_truth(tmp_path):
    repo = make(tmp_path / "repo", {
        "lib/a.js": A + "module.exports = { helper, renamed: other };\n",
        "lib/f.js": "function f() {}\nexports = module.exports = f;\nexports.extra = 1;\n",
        "lib/index.js": "exports.viaIndex = function viaIndex() {};\n",
        "lib/open.js": A + "exports.helper = helper;\nObject.assign(exports, { helper: other });\n",
        "main.js": (
            "const a = require('./lib/a');\n"  # 1
            "const { helper, renamed: again } = require('./lib/a');\n"  # 2
            "const f = require('./lib/f');\n"  # 3
            "const open = require('./lib/open');\n"  # 4
            "const path = require('path');\n"  # 5
            "function run() {\n"  # 6
            "  a.helper();\n"  # 7
            "  helper();\n"  # 8
            "  again();\n"  # 9
            "  f();\n"  # 10
            "  open.helper();\n"  # 11
            "  path.join('x');\n"  # 12
            "  require('./lib/a').renamed();\n"  # 13
            "  a.missing();\n"  # 14
            "  f.extra();\n"  # 15
            "}\n"
        ),
    })
    graph = graph_of(repo, tmp_path / "cache")
    assert callees(graph, "main.js", 7) == ["lib/a:helper"]
    assert callees(graph, "main.js", 8) == ["lib/a:helper"]
    assert callees(graph, "main.js", 9) == ["lib/a:other"], "a key that renames: renamed is other"
    assert callees(graph, "main.js", 10) == ["lib/f:f"]
    assert callees(graph, "main.js", 13) == ["lib/a:other"]
    for line in (11, 14, 15):
        assert callees(graph, "main.js", line) == [], line
    assert any("open" in r for r in reasons(graph, "main.js", 11)), reasons(graph, "main.js", 11)
    assert any("not exported" in r for r in reasons(graph, "main.js", 14))
    assert reasons(graph, "main.js", 12) == [] and graph.external_calls >= 1, "path.join is outside the repo, and no require is counted as a call"
    assert not any(u.line in (1, 2, 3, 4, 5) and u.file == "main.js" for u in graph.unresolved)


@pytest.mark.parametrize(
    "spec, files, where",
    [
        ("./x", {"x.js": "", "x.json": "{}"}, "x.js"),  # .js before .json
        ("./x", {"x.json": "{}", "x/index.js": ""}, "missing"),  # .json before the directory: data, no code
        ("./x", {"x/index.js": ""}, "x/index.js"),
        ("./x/", {"x.js": "", "x/index.js": ""}, "x/index.js"),  # a trailing slash is a directory
        ("./x.js", {"x.js": ""}, "x.js"),
        ("./x", {"x.jsx": ""}, None),  # never tried by require
        ("./x", {"x.mjs": ""}, None),
        ("./x", {"x/package.json": '{"main": "index.js"}', "x/index.js": ""}, "unresolved"),  # main is not read
        ("./x", {"x/package.json": '{"module": "m.js"}', "x/index.js": ""}, "unresolved"),
        ("./x", {"x/package.json": '{"name": "x"}', "x/index.js": ""}, "x/index.js"),  # a package.json with no entry field is no package.json
        ("./x.json", {"x.json": "{}"}, "missing"),
        ("./x", {"x.ts": "export const a = 1;"}, None),
        ("fs", {}, "package"),
    ],
)
def test_require_resolves_in_nodes_order(tmp_path, spec, files, where):
    repo = make(tmp_path / "repo", {"main.js": "", **files})
    modules = ModuleMap(repo, find_source_files(repo, JAVASCRIPT))
    kind, value = modules.resolve_require(spec, "main.js")
    if where in ("unresolved", "missing", "package"):
        assert kind == where, (kind, value)
    elif where is None:
        assert kind in ("missing", "unresolved"), (kind, value)
    else:
        assert kind == "repo" and modules.rel_of(value) == where, (kind, value)


def test_a_file_is_commonjs_by_its_extension_and_the_nearest_package_json(tmp_path):
    repo = make(tmp_path / "repo", {
        "package.json": json.dumps({"type": "module"}),
        "a.js": "", "b.cjs": "", "c.mjs": "", "d.jsx": "",
        "sub/package.json": json.dumps({"type": "commonjs"}), "sub/e.js": "",
        "bare/f.js": "",
    })
    (repo / "bare").mkdir(exist_ok=True)
    modules = ModuleMap(repo, find_source_files(repo, JAVASCRIPT))
    assert {rel: modules.node_format(rel) for rel in ("a.js", "b.cjs", "c.mjs", "d.jsx", "sub/e.js", "bare/f.js")} == {
        "a.js": "esm", "b.cjs": "cjs", "c.mjs": "esm", "d.jsx": "none", "sub/e.js": "cjs", "bare/f.js": "esm",
    }
    loose = make(tmp_path / "loose", {"a.js": ""})
    assert ModuleMap(loose, find_source_files(loose, JAVASCRIPT)).node_format("a.js") == "detect"


def test_es_imports_of_a_commonjs_file_are_not_followed(tmp_path):
    repo = make(tmp_path / "repo", {
        "lib/a.cjs": A + "module.exports = { helper };\n",
        "main.mjs": "import a from './lib/a.cjs';\nimport { helper } from './lib/a.cjs';\nimport * as ns from './lib/a.cjs';\nexport { helper as again } from './lib/a.cjs';\na.helper();\nhelper();\nns.helper();\n",
        "second.mjs": "import { again } from './main.mjs';\nagain();\n",
    })
    graph = graph_of(repo, tmp_path / "cache")
    for line in (5, 6, 7):
        assert callees(graph, "main.mjs", line) == [] and any("CommonJS" in r for r in reasons(graph, "main.mjs", line)), line
    assert callees(graph, "second.mjs", 2) == []


def test_an_es_default_or_namespace_import_opens_the_exports_to_the_other_files(tmp_path):
    repo = make(tmp_path / "repo", {
        "lib/a.cjs": A + "module.exports = { helper };\n",
        "main.cjs": "const a = require('./lib/a.cjs');\na.helper();\n",
        "patch.mjs": "import a from './lib/a.cjs';\na.helper = () => 1;\n",
    })
    graph = graph_of(repo, tmp_path / "cache")
    assert callees(graph, "main.cjs", 2) == [] and any("open" in r for r in reasons(graph, "main.cjs", 2))


def test_a_named_es_import_of_a_commonjs_file_does_not_open_it(tmp_path):
    repo = make(tmp_path / "repo", {
        "lib/a.cjs": A + "module.exports = { helper };\n",
        "main.cjs": "const a = require('./lib/a.cjs');\na.helper();\n",
        "reader.mjs": "import { helper } from './lib/a.cjs';\nhelper();\n",
    })
    graph = graph_of(repo, tmp_path / "cache")
    assert callees(graph, "main.cjs", 2) == ["lib/a:helper"]
    assert callees(graph, "reader.mjs", 2) == []


def test_a_typescript_file_beside_means_what_it_does_to_exports_is_not_known(tmp_path):
    files = {"lib/a.js": A + "module.exports = { helper };\n", "main.js": "const a = require('./lib/a');\na.helper();\n"}
    plain = graph_of(make(tmp_path / "plain", files), tmp_path / "cache1", lang="javascript")
    assert callees(plain, "main.js", 2) == ["lib/a:helper"]
    mixed = graph_of(make(tmp_path / "mixed", {**files, "src/x.ts": "export const x = 1;\n"}), tmp_path / "cache2", lang="javascript")
    assert callees(mixed, "main.js", 2) == [] and any("TypeScript" in r for r in reasons(mixed, "main.js", 2))


@pytest.mark.parametrize("mock", ["jest.mock('./lib/a');", "vi.mock('./lib/a', () => ({}));", "mockRequire('./lib/a', {});"])
def test_a_mock_call_that_names_the_file_opens_its_exports(tmp_path, mock):
    repo = make(tmp_path / "repo", {
        "lib/a.js": A + "module.exports = { helper };\n",
        "main.js": f"const a = require('./lib/a');\n{mock}\na.helper();\n",
    })
    graph = graph_of(repo, tmp_path / "cache")
    assert callees(graph, "main.js", 3) == [] and any("mock" in r for r in reasons(graph, "main.js", 3))


def test_a_dynamic_import_of_the_file_opens_it_and_a_computed_one_opens_everything(tmp_path):
    files = {"lib/a.js": A + "module.exports = { helper };\n", "main.js": "const a = require('./lib/a');\na.helper();\n"}
    literal = graph_of(make(tmp_path / "literal", {**files, "later.mjs": "const m = await import('./lib/a.js');\n"}), tmp_path / "c1")
    assert callees(literal, "main.js", 2) == []
    computed = graph_of(make(tmp_path / "computed", {**files, "later.mjs": "export async function load(name) { return await import(name); }\n"}), tmp_path / "c2")
    assert callees(computed, "main.js", 2) == []


def test_a_json_file_that_appears_changes_the_answer_the_cache_gave(tmp_path):
    repo = make(tmp_path / "repo", {"lib/a/index.js": A + "module.exports = { helper };\n", "main.js": "const a = require('./lib/a');\na.helper();\n"})
    cache = tmp_path / "cache"
    assert callees(graph_of(repo, cache), "main.js", 2) == ["lib/a/index:helper"] or callees(graph_of(repo, cache), "main.js", 2) == ["lib/a:helper"]
    (repo / "lib" / "a.json").write_text("{}")
    after = graph_of(repo, cache)
    assert callees(after, "main.js", 2) == [] and any("JSON" in r for r in reasons(after, "main.js", 2))


def test_typescript_files_are_read_as_they_always_were(tmp_path):
    """TS and JS share this front end: a .ts file that writes CommonJS gets nothing new (a require is the call it always was)."""
    repo = make(tmp_path / "repo", {
        "lib/a.ts": "export function helper() { return 1; }\n",
        "main.ts": "const a = require('./lib/a');\nconst fs = require('fs');\na.helper();\nfs.readFileSync('x');\nmodule.exports = { a };\n",
    })
    graph = graph_of(repo, tmp_path / "cache", lang="typescript")
    assert callees(graph, "main.ts", 3) == []
    assert any("outside the repo" in r for r in reasons(graph, "main.ts", 3)), reasons(graph, "main.ts", 3)
    assert graph.external_calls >= 2, "each require stays a call to a builtin"


def test_a_file_that_is_es_by_its_syntax_is_not_commonjs(tmp_path):
    repo = make(tmp_path / "repo", {
        "lib/a.js": "export function helper() {}\nmodule.exports = {};\n",
        "main.js": "const a = require('./lib/a');\na.helper();\n",
    })
    graph = graph_of(repo, tmp_path / "cache")
    assert callees(graph, "main.js", 2) == [] and any("ES module" in r for r in reasons(graph, "main.js", 2))


def test_a_require_cycle_is_open_only_for_the_file_that_replaces_its_exports(tmp_path):
    repo = make(tmp_path / "repo", {
        "a.js": "const b = require('./b');\nfunction x() {}\nmodule.exports = { x };\n",
        "b.js": "const a = require('./a');\nfunction y() {}\nexports.y = y;\nfunction run() { a.x(); }\n",
        "c.js": "const b = require('./b');\nb.y();\n",
    })
    graph = graph_of(repo, tmp_path / "cache")
    assert callees(graph, "b.js", 4) == [], "b holds a's first exports object"
    assert callees(graph, "c.js", 2) == ["b:y"], "b only adds properties: a later call reaches y"
