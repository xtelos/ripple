"""The three answers tests/test_js_module_names.py used to mark KNOWN-WRONG, each with the shape that must keep its old answer.

Ripple draws an edge only where the source proves which function runs and says "outside the repo" only where that is proven; unresolved is
always allowed. Every row runs in Node (24.0.1 here) as well as in ripple, and a row whose Node output is not what the table says fails, so the
table is what the language does and not a guess.

  1. `process` re-exported by a repo module (`export { default } from 'process'`, `export { mainModule as pm } ...`, `export * from
     'process'`) and imported from it: `p.mainModule.exports.f()` runs the entry module's `f`. The resolver (Resolver.member and
     exported_member) ends the chain at Node's `process` or its `mainModule`, and a call that goes on through `mainModule` is unresolved, with
     the reason that says why; any other member (`p.cwd()`) stays outside the repo, and a repo export merely called `mainModule` keeps its edge.
  2. `import a = N.M` binds `a` (a `var` of the file or of the namespace body, as TypeScript emits it), so a call through it is the repo's
     value, not a builtin of that name: unresolved, never outside the repo.
  3. A class beside another declaration of its name in the same block, function, switch or file (a `var`, `let`, `const`, a function, a class,
     a parameter, a `var` in a block of it) is a SyntaxError when Node runs the text as JavaScript, so no call runs: no edge, whatever the
     name. A class beside an interface, or before a namespace, is a legal merge and keeps its answer.
  4. An `enum`, or a namespace that holds a value, at the top of the file named like a builtin (`enum process {}`): the name is the repo's, so
     `process.cwd()` is unresolved; a namespace of types only and `declare ...` emit nothing and the call stays outside the repo.
  5. `import x = require('./x')` binds `x`: unresolved or an edge as an import of the module is; a package stays a package.
"""

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from test_js_module_names import (  # noqa: E402
    DECLARED,
    GLOBAL,
    HIT,
    OWN,
    QUIET,
    SET,
    TRANSFORM,
    call_site,
    graph_of,
    node_or_skip,
    run_node,
    slug,
)

REEXPORT = "a re-export in"  # the reason of a call through `mainModule` of Node's `process` that a repo module re-exports
REDECLARED = "the same block, function or file"  # the reason of a name that a class and another declaration both declare
ENTRY = SET + "import('./b.mjs');\n"  # main.cjs holds `f` and loads b.mjs: `process.mainModule` is main.cjs


def at_site(graph, file, line):
    return [e for e in graph.edges if (e.file, e.line) == (file, line)], [u.reason for u in graph.unresolved if (u.file, u.line) == (file, line)]


def run_in_node(tmp_path, group, name, files, entry, flags=()):
    node, hook = node_or_skip(tmp_path)
    repo = hook.parent / group / slug(name)
    for rel, source in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(source)
    return run_node(node, hook, repo, entry, list(flags))


# ---- 1. `process` re-exported by a repo module

R_FILES = {
    "export { default } from 'process'": ("export { default } from 'process';\n", "import p from './r.mjs';\np.mainModule.exports.f(); // CALL\n"),
    "export * as p from 'node:process'": ("export * as p from 'node:process';\n", "import { p } from './r.mjs';\np.mainModule.exports.f(); // CALL\n"),
    "export { mainModule } from 'process'": ("export { mainModule } from 'process';\n", "import { mainModule } from './r.mjs';\nmainModule.exports.f(); // CALL\n"),
    "export { mainModule } renamed on import": ("export { mainModule } from 'process';\n", "import { mainModule as mm } from './r.mjs';\nmm.exports.f(); // CALL\n"),
    "import process, then export it": ("import pp from 'process';\nexport { pp };\n", "import { pp } from './r.mjs';\npp.mainModule.exports.f(); // CALL\n"),
    "namespace import of the re-export": ("export { default as p } from 'process';\n", "import * as ns from './r.mjs';\nns.p.mainModule.exports.f(); // CALL\n"),
    "default import, mainModule through a name of its own": ("export { default as q } from 'node:process';\n", "import { q as proc } from './r.mjs';\nproc.mainModule.exports.f(); // CALL\n"),
    "a call of mainModule's require": ("export { default } from 'process';\n", "import p from './r.mjs';\np.mainModule.require('./main.cjs').f(); // CALL\n"),
    "through a barrel": ("export { default as p } from 'process';\n", "import { p } from './s.mjs';\np.mainModule.exports.f(); // CALL\n"),
    # nothing in the importing file spells `mainModule`: the chain ends at Node's `mainModule` all the same
    "export { mainModule as pm } from 'process'": ("export { mainModule as pm } from 'process';\n", "import { pm } from './r.mjs';\npm.exports.f(); // CALL\n"),
    "export { mainModule as pm }, imported under another name": (
        "export { mainModule as pm } from 'node:process';\n",
        "import { pm as entry } from './r.mjs';\nentry.exports.f(); // CALL\n",
    ),
    "export { mainModule as pm } through a barrel": ("export { mainModule as pm } from 'process';\n", "import { pm } from './s.mjs';\npm.exports.f(); // CALL\n"),
    "export * from 'process', mainModule imported by name": ("export * from 'process';\n", "import { mainModule } from './r.mjs';\nmainModule.exports.f(); // CALL\n"),
    "export * from 'process', through a namespace": ("export * from 'node:process';\n", "import * as ns from './r.mjs';\nns.mainModule.exports.f(); // CALL\n"),
}


def r_case(label):
    r_source, b_source = R_FILES[label]
    files = {"main.cjs": ENTRY, "r.mjs": r_source, "b.mjs": b_source}
    if "./s.mjs" in b_source:
        files["s.mjs"] = "export * from './r.mjs';\n"
    return files


@pytest.mark.parametrize("label", sorted(R_FILES))
def test_a_call_through_mainmodule_of_a_reexported_process_is_not_said_to_be_outside_the_repo(tmp_path, make_repo, label):
    files = r_case(label)
    assert run_in_node(tmp_path, "reexport", label, files, "main.cjs") == HIT  # Node: the entry module's own `f`
    graph = graph_of(make_repo(files), "js")
    file, line = call_site(files)
    edges, reasons = at_site(graph, file, line)
    assert graph.external_calls == 0, graph.external_reasons
    assert not edges, edges
    assert reasons and REEXPORT in " ".join(reasons), reasons


def test_the_resolver_names_node_process_as_the_front_end_does():
    from ripple.resolver import NODE_PROCESS
    from ripple.ts_indexer import PACKAGE, PROCESS_MODULES

    assert set(NODE_PROCESS) == {PACKAGE + module for module in PROCESS_MODULES}


def test_another_member_of_a_reexported_process_is_still_outside_the_repo(tmp_path, make_repo):
    """`cwd` is Node's own, whichever repo module the `process` came through: the rule is about `mainModule` only."""
    files = {"main.cjs": ENTRY, "r.mjs": "export { default } from 'process';\n", "b.mjs": "import p from './r.mjs';\np.cwd(); // CALL\n"}
    assert run_in_node(tmp_path, "reexport", "cwd", files, "main.cjs") == QUIET
    graph = graph_of(make_repo(files), "js")
    edges, reasons = at_site(graph, "b.mjs", 2)
    assert graph.external_calls == 1 and not edges and not reasons, (graph.external_reasons, edges, reasons)


def test_a_name_imported_from_a_repo_module_keeps_its_edge_when_mainmodule_is_not_in_the_call(make_repo):
    files = {"r.mjs": "export function run() {}\nexport class App { go() {} }\n", "b.mjs": "import { run, App } from './r.mjs';\nrun();\nnew App().go();\n"}
    graph = graph_of(make_repo(files), "js")
    assert {(e.line, e.callee) for e in graph.edges if e.file == "b.mjs"} >= {(2, "r:run"), (3, "r:App.go")}, graph.edges


def test_a_parameter_named_like_the_import_hides_it(make_repo):
    """The import is not what the call goes through there, so the reason about it is not given."""
    files = {"r.mjs": "export { default } from 'process';\n", "b.mjs": "import p from './r.mjs';\nfunction g(p) { p.mainModule.exports.f(); }\n"}
    graph = graph_of(make_repo(files), "js")
    edges, reasons = at_site(graph, "b.mjs", 2)
    assert REEXPORT not in " ".join(reasons), reasons


@pytest.mark.parametrize(
    "r_source, b_source",
    [
        ("export class App { run() {} }\nexport const mainModule = new App();\n", "import { mainModule } from './r';\nmainModule.run(); // CALL\n"),
        ("export class App { run() {} }\nexport const mainModule = new App();\n", "import * as r from './r';\nr.mainModule.run(); // CALL\n"),
        ("export class App { run() {} }\nexport const mainModule = new App();\n", "import { mainModule as m } from './r';\nm.run(); // CALL\n"),
    ],
)
def test_a_repo_export_that_is_merely_called_mainmodule_keeps_its_edge(make_repo, r_source, b_source):
    """The chain ends at a class of the repo, not at Node's `process`, so the name `mainModule` means nothing here."""
    files = {"r.ts": r_source, "b.ts": b_source}
    graph = graph_of(make_repo(files), "ts")
    edges, reasons = at_site(graph, *call_site(files))
    assert [e.callee for e in edges] == ["r:App.run"] and graph.external_calls == 0, (edges, reasons)


def test_a_tag_through_mainmodule_of_a_reexported_process_is_not_counted_outside_the_repo(make_repo):
    """A JSX tag is a member read, resolved as a callee is: `<p.mainModule.Wrap />` is a member of the entry module (not run by Node here: JSX needs
    a compiler, so the answer rests on the same chain as the calls above)."""
    files = {
        "r.mjs": "export { default } from 'process';\n",
        "b.jsx": "import React from 'react';\nimport p from './r.mjs';\nexport const App = () => <p.mainModule.Wrap />;\n",
    }
    graph = graph_of(make_repo(files), "js")
    assert graph.external_calls == 0, graph.external_reasons
    assert any(u.file == "b.jsx" and REEXPORT in u.reason for u in graph.unresolved), graph.unresolved


# ---- 2. `import a = N.M`

NS = "namespace N { export namespace P { export function cwd() { Math.HIT = 'N.P.cwd'; } } }\n"
ALIAS = {
    "top of the file": (NS + "import process = N.P;\nprocess.cwd(); // CALL\n", "RESULT N.P.cwd", "unresolved"),
    "in a namespace": (NS + "namespace Z {\n  import process = N.P;\n  process.cwd(); // CALL\n}\n", "RESULT N.P.cwd", "unresolved"),
    "exported, in a namespace": (NS + "namespace Z {\n  export import process = N.P;\n  process.cwd(); // CALL\n}\n", "RESULT N.P.cwd", "unresolved"),
    "read in a function of the namespace": (
        NS + "namespace Z {\n  import process = N.P;\n  export function g() { process.cwd(); } // CALL\n}\nZ.g();\n",
        "RESULT N.P.cwd",
        "unresolved",
    ),
    "in a nested namespace": (
        NS + "namespace Z {\n  import process = N.P;\n  export namespace Y { export function g() { process.cwd(); } } // CALL\n}\nZ.Y.g();\n",
        "RESULT N.P.cwd",
        "unresolved",
    ),
    "a name that is no builtin": (NS + "import q = N.P;\nq.cwd(); // CALL\n", "RESULT N.P.cwd", "unresolved"),
    "an alias in another namespace": (NS + "namespace Z {\n  import process = N.P;\n}\nnamespace W {\n  process.cwd(); // CALL\n}\n", "RESULT NOHIT", "external"),
    "no alias at all": (NS + "namespace Z {\n  process.cwd(); // CALL\n}\n", "RESULT NOHIT", "external"),
}


@pytest.mark.parametrize("label", sorted(ALIAS))
def test_a_call_through_an_import_alias_is_the_repos_value_and_never_outside_the_repo(tmp_path, make_repo, label):
    body, node_says, want = ALIAS[label]
    files = {"main.cts": SET + body}
    assert run_in_node(tmp_path, "alias", label, files, "main.cts", TRANSFORM) == node_says
    graph = graph_of(make_repo(files), "ts")
    file, line = call_site(files)
    edges, reasons = at_site(graph, file, line)
    assert not edges, edges
    if want == "unresolved":
        assert graph.external_calls == 0, graph.external_reasons
        assert reasons and DECLARED in " ".join(reasons), reasons
    else:  # the name is a global here: Node runs the real `process.cwd`
        assert graph.external_calls == 1 and not reasons, (graph.external_reasons, reasons)


# ---- 4. an enum or a namespace at the top of the file, named like a builtin

# `enum process {}` and a namespace with a value bind `process` in the file (tsc 6.0.3 emits `var process; (function (process) {...})(process || (process = {}))`),
# so `process.cwd()` is the repo's value, not Node's. A namespace of types only, `declare namespace`, `declare enum` and `declare const enum` emit
# nothing: the name is still the global. A `const enum` is erased by tsc and bound by Node's transform, so which it is depends on settings.
TOP = {
    "enum process": ("enum process { A }\nprocess.cwd(); // CALL\n", "THROW TypeError RESULT NOHIT", "unresolved"),
    "const enum process": ("const enum process { A }\nprocess.cwd(); // CALL\n", "THROW TypeError RESULT NOHIT", "unresolved"),
    "namespace with a function": ("namespace process { export function cwd() { Math.HIT = 'ns.cwd'; } }\nprocess.cwd(); // CALL\n", "RESULT ns.cwd", "unresolved"),
    "namespace with a value, another member called": ("namespace process { export const x = 1; }\nprocess.cwd(); // CALL\n", "THROW TypeError RESULT NOHIT", "unresolved"),
    "namespace with a nested namespace of a value": (
        "namespace process { export namespace inner { export const x = 1; } }\nprocess.cwd(); // CALL\n",
        "THROW TypeError RESULT NOHIT",
        "unresolved",
    ),
    "enum console": ("enum console { A }\nconsole.log('x'); // CALL\n", "THROW TypeError RESULT NOHIT", "unresolved"),
    "namespace of types only": ("namespace process { export interface I { x: 1 } }\nprocess.cwd(); // CALL\n", QUIET, "external"),
    "namespace of types only, nested": ("namespace process { export namespace T { export type U = 1; } }\nprocess.cwd(); // CALL\n", QUIET, "external"),
    "declare namespace": ("declare namespace process { function cwd(): void; }\nprocess.cwd(); // CALL\n", QUIET, "external"),
    "declare enum": ("declare enum process { A }\nprocess.cwd(); // CALL\n", QUIET, "external"),
    "declare const enum": ("declare const enum process { A }\nprocess.cwd(); // CALL\n", QUIET, "external"),
    "interface": ("interface process { x: 1 }\nprocess.cwd(); // CALL\n", QUIET, "external"),
    "an enum of another name": ("enum Color { A }\nprocess.cwd(); // CALL\n", QUIET, "external"),
}


def check_table_row(tmp_path, make_repo, group, label, files, entry, node_says, want, node_extra=None, why=DECLARED):
    node_files = {**files, **(node_extra or {})}
    assert run_in_node(tmp_path, group, label, node_files, entry, TRANSFORM) == node_says
    graph = graph_of(make_repo(files), "ts")
    edges, reasons = at_site(graph, *call_site(files))
    assert not edges, edges
    if want == "unresolved":
        assert graph.external_calls == 0, graph.external_reasons
        assert reasons and why in " ".join(reasons), reasons
    else:
        assert graph.external_calls == 1 and not reasons, (graph.external_reasons, reasons)


@pytest.mark.parametrize("label", sorted(TOP))
def test_a_value_declared_at_the_top_of_the_file_is_not_the_builtin_of_its_name(tmp_path, make_repo, label):
    body, node_says, want = TOP[label]
    check_table_row(tmp_path, make_repo, "top", label, {"main.cts": SET + body}, "main.cts", node_says, want)


@pytest.mark.parametrize(
    "label, body, node_says, callee",
    [
        ("function", "function g() { Math.HIT = 'g'; }\nnamespace g { export const x = 1; }\ng(); // CALL\n", "RESULT g", "main:g"),
        ("class", "class K { static f() { Math.HIT = 'K.f'; } }\nnamespace K { export const x = 1; }\nK.f(); // CALL\n", "RESULT K.f", "main:K.f"),
    ],
)
def test_a_namespace_that_merges_with_a_function_or_a_class_keeps_the_answer_it_had(tmp_path, make_repo, label, body, node_says, callee):
    """The name is bound after the file is read and only if nothing bound it, so a merge keeps the binding of the function or class."""
    files = {"main.cts": SET + body}
    assert run_in_node(tmp_path, "merge", label, files, "main.cts", TRANSFORM) == node_says
    graph = graph_of(make_repo(files), "ts")
    edges, reasons = at_site(graph, *call_site(files))
    assert [e.callee for e in edges] == [callee], (edges, reasons)


# ---- 5. `import x = require('...')`

# The same construct family as `import a = N.M`: it binds `x` (tsc emits `const x = require('...')`). A repo module is read as an import of it is,
# a package stays a package, and Node's own `process` stays Node's.
CJS_X = "exports.cwd = function () { Math.HIT = 'x.cwd'; };\nexports.f = function () { Math.HIT = 'x.f'; };\n"
REQ = {
    "process from a repo module": ("import process = require('./x.cts');\nprocess.cwd(); // CALL\n", {"x.cts": CJS_X}, "RESULT x.cwd", "unresolved"),
    "a name that is no builtin": ("import lib = require('./x.cts');\nlib.f(); // CALL\n", {"x.cts": CJS_X}, "RESULT x.f", "unresolved"),
    "process.mainModule through the alias": (
        "import process = require('./x.cts');\nprocess.mainModule.exports.f(); // CALL\n",
        {"x.cts": "exports.mainModule = { exports: { f() { Math.HIT = 'x.main'; } } };\n"},
        "RESULT x.main",
        "unresolved",
    ),
    "a package": ("import x = require('pkg');\nx.f(); // CALL\n", {}, "RESULT pkg.f", "external"),
    "a package called process": ("import process = require('pkg');\nprocess.f(); // CALL\n", {}, "RESULT pkg.f", "external"),
    "Node's process": ("import process = require('process');\nprocess.cwd(); // CALL\n", {}, QUIET, "external"),
    "a node: builtin": ("import fs = require('node:fs');\nfs.existsSync('.'); // CALL\n", {}, QUIET, "external"),
}
PKG = {"node_modules/pkg/index.js": "exports.f = function () { Math.HIT = 'pkg.f'; };\n"}


@pytest.mark.parametrize("label", sorted(REQ))
def test_a_call_through_an_import_equals_require_is_what_the_module_is(tmp_path, make_repo, label):
    body, extra, node_says, want = REQ[label]
    files = {"main.cts": SET + body, **extra}
    check_table_row(tmp_path, make_repo, "req", label, files, "main.cts", node_says, want, PKG if "pkg" in body else None, why="CommonJS")  # x.cts assigns exports


def test_a_call_through_an_import_equals_require_of_a_module_with_names_is_an_edge(make_repo):
    """`import lib = require('./lib')` of a module that exports names the way an import does: the same edge `import * as lib` draws. tsc 6.0.3
    compiles both files to CommonJS (`exports.f = f`) and Node printed `RESULT lib.f` (run by hand: Node cannot run `export` in a `.cts`)."""
    files = {"main.ts": "import lib = require('./lib');\nlib.f(); // CALL\n", "lib.ts": "export function f() {}\n"}
    graph = graph_of(make_repo(files), "ts")
    edges, reasons = at_site(graph, *call_site(files))
    assert [e.callee for e in edges] == ["lib:f"] and graph.external_calls == 0, (edges, reasons)


# `import x = require(...)` is TypeScript, and only at the top level of a file: in a `.cjs` file Node throws a SyntaxError (tsc: TS8002), in a function
# body Node throws a SyntaxError (TS1232), and in a namespace tsc rejects it (TS1147) and emits no require while Node's transform stops with
# ERR_INTERNAL_ASSERTION. No program runs there, so no edge: the name is not bound.
X_CJS = "exports.go = function () { Math.HIT = 'x.go'; };\n"
REQUIRE_NOWHERE = {
    "a .cjs file": ({"main.cjs": "import x = require('./x.mjs');\nx.go(); // CALL\n", "x.mjs": "export function go() {}\n"}, "js", {}),
    "inside a namespace": (
        {"main.ts": "namespace Q { import x = require('./x'); }\nx.go(); // CALL\n", "x.ts": "export function go() {}\n"},
        "ts",
        {"main.cts": "namespace Q { import x = require('./x.cts'); }\nx.go(); // CALL\n", "x.cts": X_CJS},
    ),
    "in a function body": (
        {"main.ts": "function g() { import x = require('./x'); x.go(); } // CALL\n", "x.ts": "export function go() {}\n"},
        "ts",
        {"main.cts": "function g() { import x = require('./x.cts'); x.go(); }\ng();\n", "x.cts": X_CJS},
    ),
    "in a block": (
        {"main.ts": "{ import x = require('./x'); x.go(); } // CALL\n", "x.ts": "export function go() {}\n"},
        "ts",
        {"main.cts": "{ import x = require('./x.cts'); x.go(); }\n", "x.cts": X_CJS},
    ),
}


@pytest.mark.parametrize("label", sorted(REQUIRE_NOWHERE))
def test_an_import_equals_require_where_the_language_rejects_it_binds_nothing(tmp_path, make_repo, label):
    files, profile, node_files = REQUIRE_NOWHERE[label]
    entry = next(iter(node_files or files))
    out = run_in_node(tmp_path, "reqnowhere", label, node_files or files, entry, TRANSFORM)
    assert out.startswith(("THROW", "NOOUT")), out  # Node runs nothing there
    graph = graph_of(make_repo(files), profile)
    edges, reasons = at_site(graph, *call_site(files))
    assert not edges and graph.external_calls == 0 and reasons, (edges, graph.external_reasons, reasons)


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN-WRONG: an ESM `import` in a .cjs file or in a function body is a SyntaxError, and ripple still binds the name and draws the edge (the base does too)",
)
@pytest.mark.parametrize(
    "files, profile",
    [
        ({"main.cjs": "import * as x from './x.mjs';\nx.go(); // CALL\n", "x.mjs": "export function go() {}\n"}, "js"),
        ({"main.ts": "function g() { import * as x from './x'; x.go(); } // CALL\n", "x.ts": "export function go() {}\n"}, "ts"),
    ],
)
def test_an_esm_import_where_the_language_rejects_it_binds_nothing(tmp_path, make_repo, files, profile):
    entry = next(iter(files))
    node_entry = entry.replace("main.ts", "main.cts")
    assert run_in_node(tmp_path, "esmnowhere", entry, {node_entry: files[entry]}, node_entry, TRANSFORM).startswith(("THROW", "NOOUT"))
    graph = graph_of(make_repo(files), profile)
    edges, _ = at_site(graph, *call_site(files))
    assert not edges, edges


# `import p = require('node:process')` is Node's own `process` (tsc emits `const p = require('node:process')`): through `mainModule` it is the entry
# module, and the reason says so as the other imports of `process` do, not that a repo module re-exports it.
@pytest.mark.parametrize("spec", ["node:process", "process"])
def test_a_direct_import_equals_require_of_process_gives_the_reason_of_the_module_objects(tmp_path, make_repo, spec):
    files = {"main.cts": SET + f"import p = require('{spec}');\np.mainModule.exports.f(); // CALL\n"}
    assert run_in_node(tmp_path, "reqprocess", spec, files, "main.cts", TRANSFORM) == HIT
    graph = graph_of(make_repo(files), "ts")
    edges, reasons = at_site(graph, *call_site(files))
    assert not edges and graph.external_calls == 0, (edges, graph.external_reasons)
    assert reasons and OWN in " ".join(reasons) and REEXPORT not in " ".join(reasons), reasons


def test_a_direct_import_equals_require_of_process_keeps_its_other_members_outside_the_repo(tmp_path, make_repo):
    files = {"main.cts": SET + "import p = require('node:process');\np.cwd(); // CALL\n"}
    assert run_in_node(tmp_path, "reqprocess", "cwd", files, "main.cts", TRANSFORM) == QUIET
    graph = graph_of(make_repo(files), "ts")
    edges, reasons = at_site(graph, *call_site(files))
    assert graph.external_calls == 1 and not edges and not reasons, (graph.external_reasons, edges, reasons)


# `import globalThis = N.M` is `var globalThis = N.M` (tsc 6.0.3): the name is the repo's value, so a call through it is not read as one through the
# global object. `globalThis` and `global` are no parameters of the CommonJS wrapper, so a top-level declaration of them hides the global object.
GLOBAL_NS = (
    "namespace N { export namespace M {\n"
    "  export const module = { exports: { f() { Math.HIT = 'LOCAL'; } } };\n"
    "  export const process = { mainModule: { exports: { f() { Math.HIT = 'LOCAL'; } } } };\n"
    "} }\n"
)
GLOBAL_ALIAS = {
    "globalThis.module": "import globalThis = N.M;\nglobalThis.module.exports.f(); // CALL\n",
    "global.module": "import global = N.M;\nglobal.module.exports.f(); // CALL\n",
    "globalThis.process.mainModule": "import globalThis = N.M;\nglobalThis.process.mainModule.exports.f(); // CALL\n",
}


@pytest.mark.parametrize("label", sorted(GLOBAL_ALIAS))
def test_an_import_alias_of_a_global_object_is_not_the_global_object(tmp_path, make_repo, label):
    files = {"main.cts": SET + GLOBAL_NS + GLOBAL_ALIAS[label]}
    assert run_in_node(tmp_path, "globalalias", label, files, "main.cts", TRANSFORM) == "RESULT LOCAL"
    graph = graph_of(make_repo(files), "ts")
    edges, reasons = at_site(graph, *call_site(files))
    joined = " ".join(reasons)
    assert not edges and graph.external_calls == 0, (edges, graph.external_reasons)
    assert DECLARED in joined and GLOBAL not in joined and OWN not in joined, reasons


def test_an_import_alias_of_another_name_is_read_as_before(tmp_path, make_repo):
    files = {"main.cts": SET + GLOBAL_NS + "import q = N.M;\nq.module.exports.f(); // CALL\n"}
    assert run_in_node(tmp_path, "globalalias", "q", files, "main.cts", TRANSFORM) == "RESULT LOCAL"
    graph = graph_of(make_repo(files), "ts")
    edges, reasons = at_site(graph, *call_site(files))
    assert not edges and graph.external_calls == 0 and DECLARED in " ".join(reasons), (edges, reasons)


# ---- 3. a class beside another declaration of its name

K ="class K { static f() { Math.HIT = 'K.f'; } }"
SYNTAX_ERROR = "THROW SyntaxError RESULT NOHIT"
CLASS_CASES = {
    # (the source with the call on the line that ends // CALL, what Node prints, whether ripple keeps the edge)
    "function: class, then var": (f"function g() {{\n{K}\nvar K;\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: var, then class": (f"function g() {{\nvar K;\n{K}\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: class and let": (f"function g() {{\n{K}\nlet K = 1;\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: class and const": (f"function g() {{\n{K}\nconst K = 1;\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: class and a function": (f"function g() {{\n{K}\nfunction K() {{}}\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: a function, then class": (f"function g() {{\nfunction K() {{}}\n{K}\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: class twice": (f"function g() {{\n{K}\n{K}\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: abstract class and var": (f"function g() {{\nabstract {K}\nvar K;\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: a parameter and a class": (f"function g(K) {{\n{K}\nK.f(); // CALL\n}}\ng({{}});\n", SYNTAX_ERROR, False),
    "function: an arrow's parameter and a class": (f"((K) => {{\n{K}\nK.f(); // CALL\n}})({{}});\n", SYNTAX_ERROR, False),
    "function: a var in a nested block": (f"function g() {{\n{K}\n{{ var K = 1; }}\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: a var in a loop head": (f"function g() {{\n{K}\nfor (var K = 0; K < 1; K++) {{}}\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "function: a for-in var": (f"function g() {{\n{K}\nfor (var K in {{ a: 1 }}) {{}}\nK.f(); // CALL\n}}\ng();\n", SYNTAX_ERROR, False),
    "block: class and var": (f"function g() {{\n{{\n{K}\nvar K;\nK.f(); // CALL\n}}\n}}\ng();\n", SYNTAX_ERROR, False),
    "block: class and a var of a block inside it": (f"function g() {{\n{{\n{K}\n{{ var K = 1; }}\nK.f(); // CALL\n}}\n}}\ng();\n", SYNTAX_ERROR, False),
    "block: class twice": (f"function g() {{\n{{\n{K}\n{K}\nK.f(); // CALL\n}}\n}}\ng();\n", SYNTAX_ERROR, False),
    "block: class and a function": (f"function g() {{\n{{\n{K}\nfunction K() {{}}\nK.f(); // CALL\n}}\n}}\ng();\n", SYNTAX_ERROR, False),
    "switch: class and var": (f"function g() {{\nswitch (1) {{ case 1:\n{K}\nvar K;\nK.f(); // CALL\nbreak; }}\n}}\ng();\n", SYNTAX_ERROR, False),
    "top of the file: class twice": (f"{K}\n{K}\nK.f(); // CALL\n", SYNTAX_ERROR, False),
    "top of the file: class and var": (f"{K}\nvar K;\nK.f(); // CALL\n", SYNTAX_ERROR, False),
    "top of the file: class and a function": (f"function K() {{}}\n{K}\nK.f(); // CALL\n", SYNTAX_ERROR, False),
    "in a namespace body: class and var": (f"namespace Z {{\n{K}\nvar K;\nexport function g() {{ K.f(); }} // CALL\n}}\nZ.g();\n", SYNTAX_ERROR, False),
    "a module name: class exports and var": (
        "function g() {\nclass exports { static f() { Math.HIT = 'K.f'; } }\nvar exports;\nexports.f(); // CALL\n}\ng();\n",
        SYNTAX_ERROR,
        False,
    ),
    # a legal program keeps the edge it had
    "legal: class and an interface": (f"function g() {{\ninterface K {{ x: 1 }}\n{K}\nK.f(); // CALL\n}}\ng();\n", "RESULT K.f", True),
    # a merge ripple already left unresolved ("bound more than one way"): it is not called a redeclaration
    "legal: class, then a namespace of the name": (f"function g() {{\n{K}\nnamespace K {{ export const x = 1; }}\nK.f(); // CALL\n}}\ng();\n", "RESULT K.f", None),
    "legal: a var outside the block of the class": (f"function g() {{\nvar K = 1;\n{{\n{K}\nK.f(); // CALL\n}}\n}}\ng();\n", "RESULT K.f", True),
    "legal: a class in each of two blocks": (f"function g() {{\n{{ {K} }}\n{{\n{K}\nK.f(); // CALL\n}}\n}}\ng();\n", "RESULT K.f", True),
    "legal: a parameter of the outer function": (f"function g(K) {{\nfunction h() {{\n{K}\nK.f(); // CALL\n}}\nh();\n}}\ng({{}});\n", "RESULT K.f", True),
    "legal: a var in a function inside the block": (f"function g() {{\n{K}\nfunction h() {{ var K = 1; }}\nK.f(); // CALL\n}}\ng();\n", "RESULT K.f", True),
    "legal: a var of the same name in a function declared beside": (f"{K}\nfunction h() {{ var K = 1; }}\nK.f(); // CALL\n", "RESULT K.f", True),
    "legal: an overload of a function beside a class of another name": (f"{K}\nfunction h(): void;\nfunction h() {{}}\nK.f(); // CALL\n", "RESULT K.f", True),
}


# A row with no type syntax is run as the JavaScript file it is (`main.cjs`, no flag): the SyntaxError is the language's, and the JavaScript tsc
# emits for the same text throws it too (tsc 6.0.3, run by hand: a parameter beside a class is TS2300, and a `var` beside a class in a block
# is no tsc error but still emits the SyntaxError). The rows below need the types, so Node strips them: its type transform (swc) accepts a `var`
# beside a class in a block that plain JavaScript rejects, so no such row is run as a block here.
TYPESCRIPT_ROWS = {
    "function: abstract class and var",
    "in a namespace body: class and var",
    "legal: class and an interface",
    "legal: class, then a namespace of the name",
    "legal: an overload of a function beside a class of another name",
}


@pytest.mark.parametrize("label", sorted(CLASS_CASES))
def test_a_class_beside_a_conflicting_declaration_of_its_name_keeps_no_edge(tmp_path, make_repo, label):
    body, node_says, kept = CLASS_CASES[label]
    typed = label in TYPESCRIPT_ROWS
    files = {"main.cts" if typed else "main.cjs": SET + body}
    assert run_in_node(tmp_path, "classes", label, files, "main.cts" if typed else "main.cjs", TRANSFORM if typed else ()) == node_says
    graph = graph_of(make_repo(files), "ts" if typed else "js")
    file, line = call_site(files)
    edges, reasons = at_site(graph, file, line)
    assert graph.external_calls == 0, graph.external_reasons
    if kept is None:
        assert not edges and REDECLARED not in " ".join(reasons), (edges, reasons)
    elif kept:
        assert [e.callee for e in edges if e.callee.endswith(".f")], (edges, reasons)
    else:
        assert not edges and reasons and REDECLARED in " ".join(reasons), (edges, reasons)
