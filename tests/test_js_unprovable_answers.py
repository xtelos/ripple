"""Seven calls in JavaScript that ripple used to call external (and one it drew an edge for) and that it cannot prove either way.

An edge says "this call runs that function" and "external" says "this call runs code outside the repo"; "unresolved" is always an
acceptable answer, so each of these is now unresolved, with the reason, and is neither an edge nor an outside call. Every case was
run in Node (the reviewer's cases in the round-3 CommonJS review, and the neighbours of each):

  - a call through the module's own exports (`exports.f()`, `module.exports.f()`, `require.main.exports.f()`) runs repo code;
  - an absolute path, and a `file:` URL, name a file of the repo as readily as a relative path does;
  - a relative path that leaves the repo by `..` and walks back in names a file of the repo;
  - a relative path to a source file the walk skips (a hidden directory, the root build output) names repo code, never a package;
  - a bare name that is a Node builtin is the builtin to Node whatever `baseUrl` or `paths` map it to, but a bundler may pick the repo's
    file, so neither an edge nor an external is provable. `node:util` is always the builtin.

What each does not change is held beside it: a local that shadows `exports`, `module` or `require` is read as the local, a path that
leaves the repo and does not come back is a package, a JSON file is data, a bare name that is no builtin keeps its edge, and
TypeScript is answered as before where the rule is about JavaScript.

These need tree-sitter (the `typescript` extra) and are skipped, with the reason, without it.
"""

import json

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import JAVASCRIPT, TYPESCRIPT  # noqa: E402
from ripple.ts_modules import ModuleMap  # noqa: E402


REASON = "is a member of `exports`, `module`, `require` or `process.mainModule`"  # the reason of a call through the module's own objects


def graph_of(repo, profile=JAVASCRIPT):
    return build_graph(repo, profile)


def unresolved_in(graph, file):
    return {u.line: u.reason for u in graph.unresolved if u.file == file}


def only_unresolved(graph, file, line, words=()):
    """The site at `line` of `file` is unresolved (with every word in its reason), and the graph has no edge and no outside call."""
    reasons = unresolved_in(graph, file)
    assert line in reasons, f"{file}:{line} is not unresolved: {reasons}, edges {[(e.file, e.line, e.callee) for e in graph.edges]}"
    for word in words:
        assert word in reasons[line], reasons[line]
    assert graph.edges == [], [(e.file, e.line, e.callee) for e in graph.edges]
    assert graph.external_calls == 0, graph.external_reasons


def last_line(source):
    return len(source.rstrip("\n").splitlines())


# ---- 1 and 5: a call through the module's own exports


OWN = (
    "function f() {}\n"
    "class C {}\n"
    "exports.f = f;\n"
    "exports.C = C;\n"
    "exports.make = function () { return { run() {} }; };\n"
)
THROUGH_EXPORTS = [
    "exports.f();",
    "(exports).f();",
    "exports?.f();",
    "exports.f?.();",
    "module.exports.f();",
    "module.exports.f?.();",
    "(module.exports).f();",
    "module?.exports?.f();",
    "exports.f.call(null);",
    "exports.f.apply(null, []);",
    "new exports.C();",
    "new module.exports.C();",
    "exports.make().run();",
    "module.exports.make().run();",
    "exports.f``;",
    "require.main.exports.f();",
    "require.main.exports.make().run();",
    "require.main?.exports.f();",
    "require.resolve('./main');",
    "module.require('./main');",
    "process.mainModule.exports.f();",
]


@pytest.mark.parametrize("call", THROUGH_EXPORTS)
def test_a_call_through_exports_module_or_require_is_unresolved_never_an_outside_call(make_repo, call):
    source = OWN + call + "\n"
    graph = graph_of(make_repo({"main.js": source}))
    only_unresolved(graph, "main.js", last_line(source), [REASON])


@pytest.mark.parametrize(
    "call",
    [
        "function g() { return () => exports.f(); }",
        "class Q { run() { return module.exports.f(); } }",
        "const o = { run() { return exports.f(); } };",
    ],
)
def test_a_call_through_a_module_name_inside_a_function_or_a_class_is_unresolved_too(make_repo, call):
    source = OWN + call + "\n"
    graph = graph_of(make_repo({"main.js": source}))
    reasons = unresolved_in(graph, "main.js")
    assert REASON in reasons.get(last_line(source), ""), reasons
    assert graph.external_calls == 0 and graph.edges == []


def test_no_reason_for_a_chained_call_through_exports_says_the_repo_is_left(make_repo):
    """`exports.make().run()` reads `exports.make()` (a call of repo code, as it is not known) and a call of its result: neither
    reason may claim the result comes from code outside the repo."""
    source = OWN + "exports.make().run();\nmodule.exports.make().run().again();\n"
    graph = graph_of(make_repo({"main.js": source}))
    reasons = [u.reason for u in graph.unresolved if u.line >= 6]
    assert len(reasons) == 5 and all("a call into code outside the repo" not in reason for reason in reasons), reasons


def test_a_jsx_tag_through_exports_is_unresolved_never_a_component_from_outside_the_repo(make_repo):
    source = "function C() { return null; }\nexports.C = C;\nconst x = <exports.C />;\nconst y = <module.exports.C />;\n"
    graph = graph_of(make_repo({"main.jsx": source}))
    reasons = unresolved_in(graph, "main.jsx")
    assert {3, 4} <= set(reasons) and all(REASON in reasons[n] for n in (3, 4)), reasons
    assert graph.external_calls == 0 and graph.edges == []


ASSIGNED_K = "class K { m() { return 1; } }\n"
ASSIGNED = {
    # the reviewer's cases: each runs the module's own `exports.m` in Node, and a call is never read as a call of what a later,
    # or a conditional, assignment stores
    "a call before the assignment": ASSIGNED_K + "exports.m = function m() {};\nexports.m();\nexports = new K();\n",
    "a conditional assignment": ASSIGNED_K + "exports.m = function m() {};\nif (process.argv[2]) exports = new K();\nexports.m();\n",
    "a call in a function, assigned later": ASSIGNED_K + "module.exports.m = function m() {};\nfunction run() { exports.m(); }\nrun();\nexports = new K();\n",
    "a call after the assignment": ASSIGNED_K + "exports = new K();\nexports.m();\n",
    "an assignment twice": ASSIGNED_K + "exports = new K();\nexports = new K();\nexports.m();\n",
    "an assignment in the function that calls": ASSIGNED_K + "function g() { exports = new K(); exports.m(); }\ng();\n",
    # a top-level `var` in a CommonJS file redeclares the parameter Node runs the file with: the call above it runs the module's own
    "a top-level var after the call": ASSIGNED_K + "exports.m = function m() {};\nexports.m();\nvar exports = new K();\n",
    "a top-level var before the call": ASSIGNED_K + "var exports = new K();\nexports.m();\n",
    "a top-level var in a block": ASSIGNED_K + "exports.m = function m() {};\nexports.m();\nif (process.argv[2]) { var exports = new K(); }\n",
    "a top-level for-in var": ASSIGNED_K + "exports.m = function m() {};\nexports.m();\nfor (var exports in { a: 1 }) {}\n",
    "module": ASSIGNED_K + "module = new K();\nmodule.m();\n",
    "require": ASSIGNED_K + "require = new K();\nrequire.m();\n",
}


@pytest.mark.parametrize("name", sorted(ASSIGNED))
def test_a_bare_assignment_to_a_module_name_is_no_declaration_so_the_call_through_it_draws_no_edge(make_repo, name):
    graph = graph_of(make_repo({"main.js": ASSIGNED[name]}))
    assert "main:K.m" not in {e.callee for e in graph.edges}, [(e.line, e.callee) for e in graph.edges]
    sites = [u for u in graph.unresolved if REASON in u.reason]
    assert len(sites) == 1 and sites[0].text.endswith(".m"), graph.unresolved


def test_an_assignment_after_a_declaration_of_the_name_is_that_variable(make_repo):
    """`var exports` in a function makes the name that function's own, and an assignment to it then changes only what it holds."""
    source = ASSIGNED_K + "function g() { exports = new K(); var exports; exports.m(); }\ng();\n"
    graph = graph_of(make_repo({"main.js": source}))
    assert "main:K.m" in {e.callee for e in graph.edges}
    assert not [u for u in graph.unresolved if REASON in u.reason]


def test_a_function_declared_later_in_the_file_is_the_name_for_a_call_above_it(make_repo):
    graph = graph_of(make_repo({"main.js": "function g() { exports.m(); }\nfunction exports() {}\ng();\n"}))
    assert not [u for u in graph.unresolved if REASON in u.reason]


@pytest.mark.parametrize("suffix", [".ts", ".cts", ".mts", ".tsx"])
@pytest.mark.parametrize("call", ["exports.f();", "module.exports.f();", "require.main.exports.f();", "new exports.C();"])
def test_a_call_through_the_modules_own_objects_is_unresolved_in_a_typescript_file_too(make_repo, suffix, call):
    source = "function f() {}\nclass C {}\nexports.f = f;\nexports.C = C;\n" + call + "\n"
    graph = graph_of(make_repo({"main" + suffix: source}), TYPESCRIPT)
    only_unresolved(graph, "main" + suffix, last_line(source), [REASON])


TS_SHADOWS = {
    "a parameter": ("function g(exports: K) { exports.m(); }", "main:K.m"),
    "a const in a block": ("{ const module = new K(); module.m(); }", "main:K.m"),
    "a class of that name": ("class exports { static m() {} }\nexports.m();", "main:exports.m"),
    "a var in a function": ("function g() { var module: K = new K(); module.m(); }", "main:K.m"),
    "a function of that name": ("function require() {}\nrequire.x();", None),
    "an import of that name": ("import * as exports from './a.mjs';\nexports.f();", "a:f"),
}


@pytest.mark.parametrize("name", sorted(TS_SHADOWS))
def test_a_value_a_typescript_file_declares_under_a_module_name_is_that_value(make_repo, name):
    body, edge = TS_SHADOWS[name]
    source = "class K { m() {} }\nexport {};\n" + body + "\n"
    graph = graph_of(make_repo({"main.mts": source, "a.mts": "export function f() {}\n"}), TYPESCRIPT)
    assert not [u for u in graph.unresolved if REASON in u.reason], graph.unresolved
    if edge is not None:
        assert edge in {e.callee for e in graph.edges}


TS_NOT_VALUES = {
    "declare const": "declare const exports: any;\nexports.f();",
    "declare var": "declare var module: { exports: { f(): void } };\nmodule.exports.f();",
    "declare function": "declare function require(id: string): any;\nrequire.resolve('./main');",
    "a type alias": "type exports = { f(): void };\nexports.f();",
    "an interface": "interface module { f(): void }\nmodule.exports.f();",
    "a namespace": "namespace exports { export function h(): void {} }\nexports.h();",
    "a namespace in a function": "function g() { namespace exports { export function h(): void {} }\n exports.h(); }",
    "an enum in a function": "function g() { enum module { A }\n return module.A.toString(); }",
    "an import-equals": "import exports = require('./a.cjs');\nexports.f();",
    "a var inside a namespace": "namespace N { var exports = 1; }\nexports.f();",
    "a type import": "import type { exports } from './a.cjs';\nexports.f();",
    "a type specifier": "import { type module } from './a.cjs';\nmodule.exports.f();",
}


@pytest.mark.parametrize("name", sorted(TS_NOT_VALUES))
def test_a_declaration_that_makes_no_value_of_its_own_leaves_the_call_unresolved(make_repo, name):
    """`declare` makes no value (the name is the module's own), a type alias or an interface makes none, a namespace of the name merges
    with the module's own object (and, in a function, ripple reads no value for it), and `import x = require()` binds nothing here:
    a call through the name is unresolved and never counted outside the repo."""
    graph = graph_of(make_repo({"main.cts": TS_NOT_VALUES[name] + "\n", "a.cts": "exports.f = function () {};\n"}), TYPESCRIPT)
    assert [u for u in graph.unresolved if REASON in u.reason], graph.unresolved
    assert graph.external_calls == 0 and graph.edges == [] or "enum" in name, (graph.external_calls, graph.edges)


def test_a_call_of_require_itself_is_answered_as_before(make_repo):
    """Only a call through the loader's members (`require.main.exports`) is this rule: `require('./x')` is the loader, which is a call
    of the builtin `require` as it was, and what it returns is not followed."""
    graph = graph_of(make_repo({"main.js": "const a = require('./a');\na.f();\n", "a.js": "exports.f = function () {};\n"}))
    assert graph.external_calls == 1 and graph.edges == []
    assert "a.f" in {u.text for u in graph.unresolved}


SHADOWS = {
    "a const in a function": ("function g() { const exports = new K(); exports.m(); }", "main:K.m"),
    "a module-level const": ("const module = new K();\nmodule.m();", "main:K.m"),
    "a block-level let": ("{ let module = new K(); module.m(); }", "main:K.m"),
    "a var declared after the call": ("function g() { exports.m(); var exports = new K(); }", "main:K.m"),
    "a class of that name": ("class exports { static m() {} }\nexports.m();", "main:exports.m"),
    "a function of that name": ("function require() {}\nrequire.x();", None),
    "a parameter": ("function g(require) { require.m(); }", None),
    "an arrow parameter": ("const g = (module) => module.m();", None),
    "a destructured parameter": ("function g({ exports }) { exports.m(); }", None),
    "a catch parameter": ("try { new K(); } catch (exports) { exports.m(); }", None),
    "a function expression parameter": ("const h = function (exports) { return exports.m(); };", None),
}


@pytest.mark.parametrize("name", sorted(SHADOWS))
def test_a_local_that_shadows_exports_module_or_require_is_read_as_the_local(make_repo, name):
    body, edge = SHADOWS[name]
    source = "class K { m() {} }\n" + body + "\n"
    graph = graph_of(make_repo({"main.js": source}))
    assert not [u for u in graph.unresolved if REASON in u.reason], graph.unresolved
    if edge is not None:
        assert edge in {e.callee for e in graph.edges}


def test_an_es_module_binding_named_exports_is_the_binding(make_repo):
    repo = make_repo({"main.mjs": "import * as exports from './a.mjs';\nexports.f();\n", "a.mjs": "export function f() {}\n"})
    graph = graph_of(repo)
    assert [(e.caller, e.callee) for e in graph.edges] == [("main", "a:f")]
    assert graph.unresolved == []


# ---- 2 and 3: an absolute path and a file: URL


def importer(spec, how="named"):
    return {
        "named": f"import {{ f }} from {json.dumps(spec)};\nf();\n",
        "namespace": f"import * as ns from {json.dumps(spec)};\nns.f();\n",
        "default": f"import f from {json.dumps(spec)};\nf();\n",
        "reexport": f"export {{ f }} from {json.dumps(spec)};\n",
        "star": f"export * from {json.dumps(spec)};\n",
    }[how]


def spellings(repo):
    target = str(repo / "a.mjs")
    return [target, "file://" + target, "FILE://" + target, "  file://" + target, "file:" + target, "fi\tle://" + target]


@pytest.mark.parametrize("how", ["named", "namespace", "default"])
@pytest.mark.parametrize("which", range(6))
def test_an_absolute_path_or_a_file_url_import_is_unresolved_never_an_outside_call(make_repo, how, which):
    repo = make_repo({"a.mjs": "export function f() {}\nexport default f;\n", "main.mjs": ""})
    spec = spellings(repo)[which]
    (repo / "main.mjs").write_text(importer(spec, how))
    graph = graph_of(repo)
    only_unresolved(graph, "main.mjs", 2)


@pytest.mark.parametrize("which", range(6))
def test_a_reexport_of_an_absolute_path_or_a_file_url_leaves_the_name_unknown(make_repo, which):
    repo = make_repo({"a.mjs": "export function f() {}\n", "b.mjs": "", "main.mjs": "import { f } from './b.mjs';\nf();\n"})
    (repo / "b.mjs").write_text(importer(spellings(repo)[which], "reexport"))
    graph = graph_of(repo)
    only_unresolved(graph, "main.mjs", 2)


def test_an_absolute_path_written_with_an_escape_sequence_is_unresolved(make_repo):
    """`'\\x2ftmp/...'` is `/tmp/...` to Node; the escape is not decoded, so the path is not known."""
    repo = make_repo({"a.mjs": "export function f() {}\n"})
    target = str(repo / "a.mjs")
    spec = "\\x2f" + target[1:]
    (repo / "main.mjs").write_text(f"import {{ f }} from '{spec}';\nf();\n")
    only_unresolved(graph_of(repo), "main.mjs", 2)
    (repo / "main.mjs").write_text("import { f } from 'f\\u0069le://" + target + "';\nf();\n")
    only_unresolved(graph_of(repo), "main.mjs", 2)


def test_a_dynamic_import_or_a_require_of_an_absolute_path_is_still_unresolved(make_repo):
    repo = make_repo({"a.mjs": "export function f() {}\n"})
    target = str(repo / "a.mjs")
    (repo / "main.mjs").write_text(f"const m = await import({json.dumps(target)});\nm.f();\n")
    graph = graph_of(repo)
    assert graph.external_calls == 0 and graph.edges == [] and {2, 1} <= set(unresolved_in(graph, "main.mjs"))
    (repo / "main.js").write_text(f"const m = require({json.dumps(target)});\nm.f();\n")
    graph = graph_of(repo)
    assert graph.edges == [] and 2 in unresolved_in(graph, "main.js")
    assert graph.external_calls == 1  # the `require` call itself, as it was


def test_the_answers_for_an_absolute_path_and_a_file_url_in_the_module_map(make_repo):
    repo = make_repo({"main.js": "", "main.ts": "", "a.js": ""})
    modules = ModuleMap(repo, [repo / "main.js", repo / "a.js"])
    for spec in ("/abs/x", "file:///abs/x", "FILE:///abs/x", " file:///abs/x"):
        kind, why = modules.resolve(spec, "main.js")
        assert kind == "unresolved" and "may name a file of this repo" in why, (spec, kind, why)
    # other schemes are not paths of this machine (a data: URL is answered in test_js_resolver_answers.py)
    for spec in ("https://example.com/x.js", "node:fs"):
        assert modules.resolve(spec, "main.js")[0] == "package"
    # TypeScript is answered the same way: the compiler and Node read a path from the file system, a bundler from its root
    for spec in ("/abs/x", "file:///abs/x"):
        kind, why = modules.resolve(spec, "main.ts")
        assert kind == "unresolved" and "may name a file of this repo" in why, (spec, kind, why)


# ---- 4: a relative path that leaves the repo and comes back


def test_a_relative_import_that_leaves_the_repo_and_returns_is_unresolved_never_an_outside_call(make_repo):
    repo = make_repo({"a.mjs": "export function f() {}\n", "main.mjs": ""})
    name = repo.name
    for spec in (f"../{name}/a.mjs", f"./../{name}/a.mjs", f"../../{repo.parent.name}/{name}/a.mjs", f"../{name}/./a.mjs"):
        (repo / "main.mjs").write_text(f"import {{ f }} from {json.dumps(spec)};\nf();\n")
        only_unresolved(graph_of(repo), "main.mjs", 2, ["leaves the repo and comes back"])


def test_the_reentering_path_is_unresolved_from_a_nested_file_a_namespace_a_reexport_and_typescript(make_repo):
    repo = make_repo({"a.mjs": "export function f() {}\n", "a.ts": "export function t() {}\n", "src/deep/m.mjs": "", "main.ts": ""})
    name = repo.name
    (repo / "src/deep/m.mjs").write_text(f"import * as ns from '../../../{name}/a.mjs';\nns.f();\n")
    only_unresolved(graph_of(repo), "src/deep/m.mjs", 2)
    (repo / "src/deep/m.mjs").write_text(f"export {{ f }} from '../../../{name}/a.mjs';\n")
    (repo / "main.mjs").write_text("import { f } from './src/deep/m.mjs';\nf();\n")
    only_unresolved(graph_of(repo), "main.mjs", 2)
    (repo / "main.ts").write_text(f"import {{ t }} from '../{name}/a';\nt();\n")
    only_unresolved(graph_of(repo, TYPESCRIPT), "main.ts", 2)


def test_a_require_of_the_reentering_path_is_unresolved_as_it_was(make_repo):
    repo = make_repo({"a.js": "exports.f = function () {};\n", "main.js": ""})
    (repo / "main.js").write_text(f"const a = require('../{repo.name}/a');\na.f();\n")
    graph = graph_of(repo)
    assert graph.edges == [] and 2 in unresolved_in(graph, "main.js")


def test_a_relative_path_that_leaves_the_repo_for_good_is_still_a_package(make_repo):
    repo = make_repo({"main.mjs": "import { f } from '../not-this-repo-at-all/a.mjs';\nf();\n"})
    graph = graph_of(repo)
    assert graph.external_calls == 1 and graph.edges == [] and graph.unresolved == []
    modules = ModuleMap(repo, [repo / "main.mjs"])
    assert modules.resolve("../../../../..", "main.mjs") == ("package", "../../../../..")
    assert modules.resolve("../" + repo.name, "main.mjs")[0] == "unresolved"  # the repo's own directory


def test_a_symlink_back_into_the_repo_is_unresolved(make_repo):
    repo = make_repo({"a.mjs": "export function f() {}\n", "main.mjs": ""})
    outside = repo.parent / (repo.name + "-links")
    outside.mkdir()
    (outside / "back").symlink_to(repo)
    (repo / "main.mjs").write_text(f"import {{ f }} from '../{outside.name}/back/a.mjs';\nf();\n")
    only_unresolved(graph_of(repo), "main.mjs", 2)


# ---- 6: a relative path to source the walk skips


SKIPPED = [
    ".lib/b.mjs",
    ".hidden/deep/b.mjs",
    ".lib/b.js",
    ".lib/b.cjs",
    ".lib/b.jsx",
    ".lib/b.ts",
    ".lib/b.tsx",
    "dist/b.mjs",
    "build/b.mjs",
    "env/b.mjs",
    "venv/b.mjs",
    "site-packages/b.mjs",
    "pkg/__pycache__/b.mjs",
]


@pytest.mark.parametrize("path", SKIPPED)
@pytest.mark.parametrize("how", ["named", "namespace", "default"])
def test_a_relative_import_of_a_source_file_the_walk_skips_is_unresolved_never_a_package(make_repo, path, how):
    repo = make_repo({path: "export function f() {}\nexport default f;\n", "main.mjs": importer("./" + path, how)})
    only_unresolved(graph_of(repo), "main.mjs", 2)


def test_a_reexport_of_a_skipped_file_leaves_the_name_unknown(make_repo):
    repo = make_repo({".lib/b.mjs": "export function f() {}\n", "b.mjs": importer("./.lib/b.mjs", "reexport"), "main.mjs": "import { f } from './b.mjs';\nf();\n"})
    only_unresolved(graph_of(repo), "main.mjs", 2)
    (repo / "b.mjs").write_text(importer("./.lib/b.mjs", "star"))
    only_unresolved(graph_of(repo), "main.mjs", 2)


def test_a_typescript_file_importing_a_skipped_file_is_unresolved_too(make_repo):
    repo = make_repo({".lib/b.ts": "export function f() {}\n", "main.ts": "import { f } from './.lib/b';\nimport { f as g } from './.lib/b.ts';\nf();\ng();\n"})
    graph = graph_of(repo, TYPESCRIPT)
    assert graph.edges == [] and graph.external_calls == 0


def test_a_skipped_directory_of_a_package_or_a_data_file_is_still_a_package(make_repo):
    repo = make_repo(
        {
            "node_modules/p/y.js": "exports.f = 1;\n",
            ".lib/data.json": "{}",
            ".lib/logo.svg": "<svg/>",
            "main.mjs": (
                "import * as p from './node_modules/p/y.js';\nimport d from './.lib/data.json';\nimport s from './.lib/logo.svg';\n"
                "p.f();\nd.get();\ns.trim();\n"
            ),
        }
    )
    graph = graph_of(repo)
    assert graph.external_calls == 3 and graph.edges == [] and graph.unresolved == []


def test_a_symlink_in_node_modules_to_a_directory_of_the_repo_is_unresolved(make_repo):
    repo = make_repo({"packages/x/y.mjs": "export function f() {}\n", "main.mjs": "import { f } from './node_modules/x/y.mjs';\nf();\n"})
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x").symlink_to(repo / "packages" / "x")
    only_unresolved(graph_of(repo), "main.mjs", 2)


# ---- 7: a Node builtin name that baseUrl or paths map into the repo


BUILTIN_CONFIGS = {
    "a baseUrl in a jsconfig": ("jsconfig.json", {"compilerOptions": {"baseUrl": "."}}, "util.js"),
    "a baseUrl in a tsconfig that allows JavaScript": ("tsconfig.json", {"compilerOptions": {"allowJs": True, "baseUrl": "."}}, "util.js"),
    "a catch-all path": ("jsconfig.json", {"compilerOptions": {"baseUrl": ".", "paths": {"*": ["src/*"]}}}, "src/util.js"),
    "an exact path": ("jsconfig.json", {"compilerOptions": {"paths": {"util": ["./shims/util"]}}}, "shims/util.js"),
    "a path with a star": ("jsconfig.json", {"compilerOptions": {"baseUrl": ".", "paths": {"u*": ["lib/u*"]}}}, "lib/util.js"),
}


@pytest.mark.parametrize("name", sorted(BUILTIN_CONFIGS))
@pytest.mark.parametrize("how", ["named", "namespace", "default"])
def test_a_builtin_name_that_a_config_maps_into_the_repo_is_unresolved_never_an_edge(make_repo, name, how):
    config, body, target = BUILTIN_CONFIGS[name]
    repo = make_repo({config: json.dumps(body), target: "export function f() {}\nexport default f;\n", "main.mjs": importer("util", how)})
    only_unresolved(graph_of(repo), "main.mjs", 2, ["Node builtin"])


@pytest.mark.parametrize("module", ["fs/promises", "path/posix", "util/types", "assert/strict", "_http_agent"])
def test_a_builtin_with_a_subpath_is_a_builtin_too(make_repo, module):
    repo = make_repo(
        {"jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}), module + ".js": "export function f() {}\n", "main.mjs": importer(module)}
    )
    only_unresolved(graph_of(repo), "main.mjs", 2, ["Node builtin"])


def test_a_reexport_of_a_builtin_that_a_config_maps_into_the_repo_leaves_the_name_unknown(make_repo):
    repo = make_repo(
        {
            "jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}),
            "util.js": "export function f() {}\n",
            "b.mjs": importer("util", "reexport"),
            "main.mjs": "import { f } from './b.mjs';\nf();\n",
        }
    )
    only_unresolved(graph_of(repo), "main.mjs", 2)


def test_the_node_prefix_is_the_builtin_unless_a_config_maps_it_into_the_repo(make_repo):
    """esbuild (platform browser) bundles a repo file named `node:util.js` for `node:util` under `baseUrl: "."`; Node takes the builtin.
    With no such file, and for `node:path` here, the scheme is the builtin."""
    repo = make_repo(
        {
            "jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}),
            "util.js": "export function f() {}\n",
            "node:util.js": "export function f() {}\n",
            "main.mjs": "import { format } from 'node:util';\nformat();\n",
            "other.mjs": "import { join } from 'node:path';\njoin();\n",
        }
    )
    graph = graph_of(repo)
    assert graph.external_calls == 1 and graph.edges == []  # node:path
    assert [(u.file, "Node builtin" in u.reason) for u in graph.unresolved] == [("main.mjs", True)]


def test_a_name_that_is_no_builtin_keeps_the_edge_a_config_gives_it(make_repo):
    repo = make_repo(
        {
            "jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}),
            "helpers.js": "export function f() {}\n",
            "main.mjs": "import { f } from 'helpers';\nf();\n",
        }
    )
    graph = graph_of(repo)
    assert [(e.caller, e.callee) for e in graph.edges] == [("main", "helpers:f")]
    assert graph.external_calls == 0 and graph.unresolved == []


def test_a_builtin_no_config_maps_into_the_repo_is_still_external(make_repo):
    repo = make_repo(
        {
            "jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "src"}}),
            "util.js": "export function f() {}\n",
            "main.mjs": "import { format } from 'util';\nformat();\n",
        }
    )
    graph = graph_of(repo)  # baseUrl is src/, which holds no util file: the name is the builtin
    assert graph.external_calls == 1 and graph.edges == [] and graph.unresolved == []


def test_the_require_forms_of_a_mapped_builtin_draw_no_edge(make_repo):
    repo = make_repo(
        {
            "jsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}),
            "util.js": "exports.format = function () {};\n",
            "main.js": "const util = require('util');\nutil.format();\nrequire('util').format();\n",
        }
    )
    graph = graph_of(repo)
    assert graph.edges == []


def test_a_typescript_importer_gets_no_edge_for_a_builtin_a_config_maps_into_the_repo(make_repo):
    """Node takes the builtin and `tsc` leaves the specifier as written, so an edge to the repo's util.ts is one Node never runs."""
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}),
            "util.ts": "export function format() {}\nexport default format;\n",
            "main.ts": "import { format } from 'util';\nformat();\n",
            "ns.ts": "import * as util from 'util';\nutil.format();\n",
            "def.ts": "import fmt from 'util';\nfmt();\n",
            "ok.ts": "import { format } from 'node:util';\nformat();\n",
            "node:util.ts": "export function format() {}\n",  # a file named like the scheme: esbuild (browser) bundles it, Node takes the builtin
            "path.ts": "import { join } from 'node:path';\njoin();\n",
        }
    )
    graph = graph_of(repo, TYPESCRIPT)
    assert graph.edges == []
    assert {u.file for u in graph.unresolved if "Node builtin" in u.reason} == {"main.ts", "ns.ts", "def.ts", "ok.ts"}
    assert graph.external_calls == 1  # node:path, which no file of the repo is named like


def test_a_typescript_name_that_is_no_builtin_keeps_the_edge_a_config_gives_it(make_repo):
    repo = make_repo(
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": "."}}),
            "helpers.ts": "export function f() {}\n",
            "main.ts": "import { f } from 'helpers';\nf();\n",
        }
    )
    assert [(e.caller, e.callee) for e in graph_of(repo, TYPESCRIPT).edges] == [("main", "helpers:f")]


# ---- an entry of the repo whose name starts with two dots is inside the repo


def test_an_entry_called_dot_dot_x_is_read_like_any_relative_path(make_repo):
    """`./..x/a.mjs` does not leave the repo: a hidden directory is source the walk skips (unresolved), a file at the root named
    `..lib.mjs` is read, and Node ran the file in each case."""
    repo = make_repo(
        {
            "..x/a.mjs": "export function f() {}\n",
            "..x/index.mjs": "export function g() {}\n",
            "..lib.mjs": "export function h() {}\n",
            "a.mjs": "import { f } from './..x/a.mjs';\nf();\n",
            "b.mjs": "import { g } from './..x/index.mjs';\ng();\n",
            "c.mjs": "import * as dir from './..x/';\ndir.g();\n",
            "d.mjs": "import { h } from './..lib.mjs';\nh();\n",
            "e.ts": "import { f } from './..x/a';\nf();\n",
        }
    )
    graph = graph_of(repo)
    assert graph.external_calls == 0
    assert {u.file for u in graph.unresolved} == {"a.mjs", "b.mjs", "c.mjs"}
    assert [(e.file, e.caller, e.callee) for e in graph.edges] == [("d.mjs", "d", "..lib:h")]
    ts = graph_of(repo, TYPESCRIPT)
    assert ts.external_calls == 0 and ts.edges == [] and {u.file for u in ts.unresolved} == {"e.ts"}


def test_a_path_that_names_dot_dot_x_and_nothing_is_missing_not_a_package_and_a_data_file_is_a_package(make_repo):
    repo = make_repo({"..x/d.json": "{}", "main.mjs": "import d from './..x/d.json';\nimport { f } from './..nothing/a.mjs';\nd.get();\nf();\n"})
    modules = ModuleMap(repo, [repo / "main.mjs"])
    assert modules.resolve("./..x/d.json", "main.mjs") == ("package", "./..x/d.json")
    assert modules.resolve("./..nothing/a.mjs", "main.mjs") == ("missing", "./..nothing/a.mjs")
    assert modules.resolve("../x", "main.mjs") == ("package", "../x")  # above the root
