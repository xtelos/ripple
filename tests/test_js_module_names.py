"""How a call is answered when it goes through the module objects (`module`, `exports`, `require`, `process.mainModule`) under another spelling, or
through a declaration that is named like one.

Ripple draws an edge only where the source proves which function runs and says "outside the repo" only where that is proven; unresolved is
always allowed. Each case below is a small repo, and the table holds what Node 24.0.1 printed when it ran the entry file (`node`; `--require`
of a hook that prints `RESULT <what ran>` at exit and `THROW <error class>` for an uncaught error). The tests run every case in Node again when
Node 22 or later is installed. A line that ends `// CALL` is the call the case asks about.

  1. The module objects reached through the global object or an import of `process`. Node:
       - `globalThis.process.mainModule.exports.f()`, `global.process.mainModule...` and `process` (or `mainModule`) imported from `process` or
         `node:process`, as a default, a namespace or a name: `f` runs (the entry module is this repo's file). Answered unresolved, never
         outside the repo, where main said outside.
       - `globalThis.module`, `globalThis.exports` and `globalThis.require` THROW in a file Node runs: `module`, `exports` and `require` are
         parameters of the function Node wraps the file in, not properties of the global object. They are unresolved with a reason that says
         so, because code may store a module object on the global object (`globalThis.module = module` makes the call run `f`), and never
         outside the repo for that reason.
  2. A declaration named like a module name, in a function or a block. What exists at run time decides:
       - `interface`, `type`, and a `declare` form are erased, so `module.exports.f()` in the same function is the module's own and runs `f`: the
         answer stays "unresolved, a member of the module objects". An enum IS a binding (Node, with `--experimental-transform-types`, throws
         there because `module.exports` is not the module's), so the call is unresolved with the reason that the name is declared there. A
         `const enum` is erased by tsc and bound by Node's transform and by esbuild, so it depends on settings ripple does not read: unresolved.
       - A block enum named like a parameter hides it only inside the block.
  3. A global object that a declaration gives a value of its own (a top-level `var global`, a parameter `globalThis`, a `let` in a block) is not
     the global object: the call is read as written, and no reason says it is the module's own.
  4. The reason a child of `<exports.Wrap>` has no renderer says the tag is not known to be a component, which is true; "is not a component of
     the repo" was not proven for a tag ripple cannot name.
  5. The scope of each kind of declaration (`var`, `let`, `const`, a function, `class`, `enum`, `const enum`, `namespace`, a parameter, a catch
     binding, and an erased declaration beside any of them), in each position, every row run in Node, and the grammar's statement-level kinds
     all classified (a `namespace` is held in an expression statement, `export`, `declare` and a label hold the rest).
  6. `process` re-exported by a repo module is still answered outside the repo, which is wrong: four rows, each a strict xfail marked
     KNOWN-WRONG (the rule needs ripple/resolver.py).

The Node rows run when Node can run a TypeScript file with no flag and with `--experimental-transform-types`, which the test finds by trying
both on a small file (type stripping is on by default from Node 23.6 and, by its release notes, 22.18, which is not run here: only Node 12,
which skips the rows, and Node 24.0.1 are installed).

These need tree-sitter (the `typescript` extra) and are skipped, with the reason, without it.
"""

import shutil
import subprocess

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import JAVASCRIPT, TYPESCRIPT  # noqa: E402

OWN = "a member of `exports`, `module`, `require` or `process.mainModule`"  # the reason of a call through the module's own objects
GLOBAL = "is reached through `globalThis` or `global` as a member of `module`, `exports` or `require`"  # the reason through the global object
DECLARED = "declared here, which ripple does not follow"  # the reason of a call through a name a declaration binds
SET = "function f() { Math.HIT = 'main:f'; }\nexports.f = f;\n"
LOCAL = (
    "{ module: { exports: { f() { Math.HIT = 'LOCAL'; } } }, process: { mainModule: { exports: { f() { Math.HIT = 'LOCAL'; } } } } }"
)
TRANSFORM = ["--experimental-transform-types"]  # an enum needs it: Node strips types only, which an enum is not


def graph_of(repo, profile):
    return build_graph(repo, TYPESCRIPT if profile == "ts" else JAVASCRIPT)


def reasons_at(graph, file, line):
    return [u.reason for u in graph.unresolved if (u.file, u.line) == (file, line)]


def call_site(files):
    """(file, line) of the one line that ends `// CALL`."""
    found = [(rel, n) for rel, source in files.items() for n, text in enumerate(source.splitlines(), 1) if text.rstrip().endswith("// CALL")]
    assert len(found) == 1, found
    return found[0]


CASES: dict = {}


def case(name, files, node, want, entry="main.cjs", profile="js", args=(), known=""):
    """`known` is why ripple's answer is wrong where the row says what it should be (the ripple test is then a strict xfail: KNOWN-WRONG)."""
    CASES[name] = {"files": files, "entry": entry, "node": node, "want": want, "profile": profile, "args": list(args), "known": known}


def cjs(name, body, node, want, prefix=SET):
    case(name, {"main.cjs": prefix + body}, node, want)


def esm(name, body, node, want, entry_extra=""):
    """main.cjs holds `f` and imports b.mjs, which holds the call: `process.mainModule` is main.cjs."""
    case(name, {"main.cjs": SET + "import('./b.mjs');\n" + entry_extra, "b.mjs": body}, node, want)


def ts(name, body, node, want, args=(), prefix=SET):
    case(name, {"main.cts": prefix + body}, node, want, entry="main.cts", profile="ts", args=args)


HIT, QUIET, TYPE_ERROR, LOCAL_HIT = "RESULT main:f", "RESULT NOHIT", "THROW TypeError RESULT NOHIT", "RESULT LOCAL"
UNRES_OWN = ("unresolved", OWN)
UNRES_GLOBAL = ("unresolved", GLOBAL)

# ---- 1. the global object, in a CommonJS file
cjs("globalThis.process.mainModule", "globalThis.process.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
cjs("global.process.mainModule", "global.process.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
cjs("globalThis.global.process.mainModule", "globalThis.global.process.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
cjs("(globalThis).process.mainModule", "(globalThis).process.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
cjs("globalThis?.process?.mainModule", "globalThis?.process?.mainModule?.exports.f(); // CALL\n", HIT, UNRES_OWN)
cjs("globalThis.module throws", "globalThis.module.exports.f(); // CALL\n", TYPE_ERROR, UNRES_GLOBAL)
cjs("global.module throws", "global.module.exports.f(); // CALL\n", TYPE_ERROR, UNRES_GLOBAL)
cjs("globalThis.exports throws", "globalThis.exports.f(); // CALL\n", TYPE_ERROR, UNRES_GLOBAL)
cjs("globalThis.require throws", "globalThis.require.main.exports.f(); // CALL\n", TYPE_ERROR, UNRES_GLOBAL)
cjs("new globalThis.module throws", "new globalThis.module.exports.C(); // CALL\n", TYPE_ERROR, UNRES_GLOBAL)
cjs("globalThis.module stored by the code", "globalThis.module = module;\nglobalThis.module.exports.f(); // CALL\n", HIT, UNRES_GLOBAL)
cjs("global.module stored, through globalThis.global", "global.module = module;\nglobal.globalThis.module.exports.f(); // CALL\n", HIT, UNRES_GLOBAL)
cjs("module name hidden, global property kept", "function g(module) { module.x; return globalThis.module.exports.f(); } // CALL\ng({});\n", TYPE_ERROR, UNRES_GLOBAL)
# other members of the global object and of process are outside the repo, as they were
cjs("globalThis.console.log", "globalThis.console.log('x'); // CALL\n", QUIET, ("external",))
cjs("globalThis.process.cwd", "globalThis.process.cwd(); // CALL\n", QUIET, ("external",))
# ---- 3. a global object that a declaration gives a value of its own
cjs("const globalThis", "const globalThis = " + LOCAL + ";\nglobalThis.module.exports.f(); // CALL\n", LOCAL_HIT, ("local",))
cjs("parameter global", "function g(global) { global.module.exports.f(); } // CALL\ng(" + LOCAL + ");\n", LOCAL_HIT, ("local",))
cjs("parameter globalThis", "function g(globalThis) { globalThis.module.exports.f(); } // CALL\ng(" + LOCAL + ");\n", LOCAL_HIT, ("local",))
cjs("let global in a block", "{ let global = " + LOCAL + "; global.module.exports.f(); } // CALL\n", LOCAL_HIT, ("local",))
cjs("top-level var global", "var global = " + LOCAL + ";\nglobal.module.exports.f(); // CALL\n", LOCAL_HIT, ("local",))
cjs("top-level var globalThis", "var globalThis = " + LOCAL + ";\nglobalThis.module.exports.f(); // CALL\n", LOCAL_HIT, ("local",))
cjs("var global in a block", "if (1) { var global = " + LOCAL + "; }\nglobal.module.exports.f(); // CALL\n", LOCAL_HIT, ("local",))
cjs("function global", "function global() {}\nglobal.module = " + LOCAL + ".module;\nglobal.module.exports.f(); // CALL\n", LOCAL_HIT, ("local",))
# a bare assignment declares nothing, so the name may still be the global object when the call runs: not known, unresolved
cjs("bare assignment to global", "global = " + LOCAL + ";\nglobal.module.exports.f(); // CALL\n", LOCAL_HIT, UNRES_GLOBAL)
# ---- the process imports, in an ES module file that main.cjs loads
esm("import process from 'process'", "import process from 'process';\nprocess.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("import process from 'node:process'", "import process from 'node:process';\nprocess.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("import * as ns from 'node:process'", "import * as ns from 'node:process';\nns.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("ns.default", "import * as ns from 'node:process';\nns.default.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("ns.default of 'process'", "import * as ns from 'process';\nns.default.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("import { mainModule } from 'process'", "import { mainModule } from 'process';\nmainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("import { default as p }", "import { default as p2 } from 'node:process';\np2.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("import { mainModule as mm }", "import { mainModule as mm } from 'node:process';\nmm.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("import process, { mainModule }", "import process, { mainModule } from 'node:process';\nmainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("import { cwd } from 'process'", "import { cwd } from 'process';\ncwd(); // CALL\n", QUIET, ("external",))
esm("ns.cwd", "import * as ns from 'node:process';\nns.cwd(); // CALL\n", QUIET, ("external",))
esm("process import hidden by a parameter", "import process from 'node:process';\nfunction g(process) { process.mainModule.exports.f(); } // CALL\ng(" + LOCAL + ".process);\n", LOCAL_HIT, ("local",))
esm("globalThis.process in an ES module", "globalThis.process.mainModule.exports.f(); // CALL\n", HIT, UNRES_OWN)
esm("globalThis.module in an ES module throws", "globalThis.module.exports.f(); // CALL\n", TYPE_ERROR, UNRES_GLOBAL)
# `process` re-exported by a repo module, and imported from it: Node runs the entry module's `f`; the answer is external (KNOWN-WRONG). The
# front end of one file sees `p` come from './r.mjs', and the resolver follows the re-export to a name imported from outside the repo, where
# nothing says that name is Node's `process`; the rule needs the resolver (ripple/resolver.py: a package target `process` or `node:process`
# whose member is `mainModule`), which this change does not touch.
RE_EXPORT_WHY = "a repo module re-exports Node's process, and the resolver ends the chain at a name imported from outside the repo"
for label, r_source, b_source in (
    ("export { default } from 'process'", "export { default } from 'process';\n", "import p from './r.mjs';\np.mainModule.exports.f(); // CALL\n"),
    ("export * as p from 'node:process'", "export * as p from 'node:process';\n", "import { p } from './r.mjs';\np.mainModule.exports.f(); // CALL\n"),
    ("export { mainModule } from 'process'", "export { mainModule } from 'process';\n", "import { mainModule } from './r.mjs';\nmainModule.exports.f(); // CALL\n"),
    ("import process, then export it", "import pp from 'process';\nexport { pp };\n", "import { pp } from './r.mjs';\npp.mainModule.exports.f(); // CALL\n"),
):
    case(
        "re-exported process: " + label,
        {"main.cjs": SET + "import('./b.mjs');\n", "r.mjs": r_source, "b.mjs": b_source},
        HIT,
        UNRES_OWN,
        known=RE_EXPORT_WHY,
    )
# ---- 2. a declaration named like a module name
ENUM_THROWS = "THROW TypeError RESULT NOHIT"
ts("interface module in a function", "function g() { interface module { x: number } module.exports.f(); } // CALL\ng();\n", HIT, UNRES_OWN)
ts("interface module in a block", "function g() { if (1) { interface module { x: number } module.exports.f(); } } // CALL\ng();\n", HIT, UNRES_OWN)
ts("type module in a function", "function g() { type module = number; module.exports.f(); } // CALL\ng();\n", HIT, UNRES_OWN)
ts("type module in a block", "{ type module = number; module.exports.f(); } // CALL\n", HIT, UNRES_OWN)
ts("type module in a switch", "switch (1) { case 1: type module = number; module.exports.f(); break; } // CALL\n", HIT, UNRES_OWN)
ts("interface exports in a function", "function g() { interface exports { x: 1 } exports.f(); } // CALL\ng();\n", HIT, UNRES_OWN)
ts("interface require in a function", "function g() { interface require { x: 1 } require.main.exports.f(); } // CALL\ng();\n", HIT, UNRES_OWN)
ts("type process in a function", "function g() { type process = number; process.mainModule.exports.f(); } // CALL\ng();\n", HIT, UNRES_OWN)
ts("interface globalThis in a function", "function g() { interface globalThis { x: 1 } globalThis.process.mainModule.exports.f(); } // CALL\ng();\n", HIT, UNRES_OWN)
ts("namespace of types only in a function", "function g() { namespace module { export interface I { x: 1 } } module.exports.f(); } // CALL\ng();\n", HIT, UNRES_OWN)
ts("declare const module", "declare const module: any;\nmodule.exports.f(); // CALL\n", HIT, UNRES_OWN)
ts("enum module in a function", "function g() { enum module { A } return module.exports.f(); } // CALL\ng();\n", ENUM_THROWS, ("unresolved", DECLARED), TRANSFORM)
ts("enum module in a block", "function g() { if (1) { enum module { A } module.exports.f(); } } // CALL\ng();\n", ENUM_THROWS, ("unresolved", DECLARED), TRANSFORM)
ts("const enum module in a function", "function g() { const enum module { A } return module.exports.f(); } // CALL\ng();\n", ENUM_THROWS, ("unresolved", DECLARED), TRANSFORM)
ts("enum exports in a function", "function g() { enum exports { A } return exports.f(); } // CALL\ng();\n", ENUM_THROWS, ("unresolved", DECLARED), TRANSFORM)
ts("enum process in a function", "function g() { enum process { A } return process.mainModule.exports.f(); } // CALL\ng();\n", ENUM_THROWS, ("unresolved", DECLARED), TRANSFORM)
ts("class module in a function", "function g() { class module {} return module.exports.f(); } // CALL\ng();\n", ENUM_THROWS, ("local",))
ts("interface and a const of that name", "function g() { interface module { x: 1 } const module = " + LOCAL + ".module; module.exports.f(); } // CALL\ng();\n", LOCAL_HIT, ("local",))
ts("enum in a function, interface in a nested block", "function g() { enum module { A } { interface module { x: 1 } } return module.exports.f(); } // CALL\ng();\n", ENUM_THROWS, ("unresolved", DECLARED), TRANSFORM)
# an enum in a block hides a parameter of that name only inside the block (the case is run in Node as a whole)
ts(
    "block enum over a parameter",
    "class K { m() { Math.HIT = 'main:K.m'; } }\n"
    "function g(module: K) {\n"
    "  if (module) {\n"
    "    enum module { A }\n"
    "  }\n"
    "  module.m(); // CALL\n"
    "}\n"
    "g(new K());\n",
    "RESULT main:K.m",
    ("edge", "main:K.m"),
    TRANSFORM,
    prefix="",
)


def run_ripple(make_repo, name):
    spec = CASES[name]
    graph = graph_of(make_repo(spec["files"]), spec["profile"])
    file, line = call_site(spec["files"])
    return spec, graph, file, line


def known_wrong(name):
    return pytest.param(name, marks=pytest.mark.xfail(strict=True, reason="KNOWN-WRONG: " + CASES[name]["known"])) if CASES[name]["known"] else name


@pytest.mark.parametrize("name", [known_wrong(n) for n in sorted(CASES)])
def test_ripple_answers_the_case_as_the_table_says(make_repo, name):
    spec, graph, file, line = run_ripple(make_repo, name)
    want, reasons = spec["want"], reasons_at(graph, file, line)
    at_site = [e for e in graph.edges if (e.file, e.line) == (file, line)]
    shown = (reasons, [(e.file, e.line, e.callee) for e in graph.edges], graph.external_calls, graph.external_reasons)
    if want[0] == "unresolved":
        assert reasons and all(word in " ".join(reasons) for word in want[1:]), shown
        assert not at_site and graph.external_calls == 0, shown
    elif want[0] == "external":
        assert graph.external_calls >= 1 and not at_site, shown
        assert OWN not in " ".join(reasons) and GLOBAL not in " ".join(reasons), shown
    elif want[0] == "local":  # the name is the declaration's value: read as written, and never said to be the module's own
        assert OWN not in " ".join(reasons) and GLOBAL not in " ".join(reasons), shown
        assert graph.external_calls == 0 and not at_site, shown
    else:
        assert (file, line, want[1]) in {(e.file, e.line, e.callee) for e in graph.edges}, shown


HOOK = (
    "process.on('uncaughtException', (e) => { console.log('THROW', e && e.constructor && e.constructor.name); });\n"
    "process.on('unhandledRejection', (e) => { console.log('THROW', e && e.constructor && e.constructor.name); });\n"
    "process.on('exit', () => { console.log('RESULT', Math.HIT === undefined ? 'NOHIT' : Math.HIT); });\n"
)


def node_or_skip(tmp_path):
    """(the node binary, the hook file) when Node can run the rows, else a skip. The rows run `node main.cts` (types stripped, no flag) and
    `--experimental-transform-types` (an enum, a namespace with a value), and `process.mainModule`: the gate is what Node does, tried on a file,
    not a version number (type stripping is on by default from 23.6 and, by its release notes, 22.18; the transform flag is older)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    work = tmp_path.with_name(tmp_path.name + "-node")  # beside the repo a test indexes, never in it: the files Node runs hold calls too
    probe = work / "probe"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "strip.cts").write_text("const x: number = 1;\nconsole.log('strip', x);\n")
    (probe / "transform.cts").write_text("enum E { A }\nconsole.log('transform', E.A);\n")
    for file, flags, expected in (("strip.cts", [], "strip 1"), ("transform.cts", ["--experimental-transform-types"], "transform 0")):
        run = subprocess.run([node, "--no-warnings", *flags, file], cwd=probe, capture_output=True, text=True, timeout=60)
        if run.stdout.strip() != expected:
            pytest.skip(f"this node cannot run TypeScript files as the rows need ({file} printed {run.stdout.strip()!r}: {run.stderr.strip()[-80:]})")
    hook = work / "hook.cjs"
    hook.write_text(HOOK)
    return node, hook


def run_node(node, hook, repo, entry, args):
    run = subprocess.run([node, "--no-warnings", "--require", str(hook), *args, entry], cwd=repo, capture_output=True, text=True, timeout=60)
    out = " ".join(" ".join(line.split()[:2]) if line.startswith("THROW") else line for line in run.stdout.splitlines() if line.startswith(("THROW", "RESULT")))
    return out or "NOOUT " + (run.stderr.strip().splitlines() or [""])[-1][:80]


def slug(name):
    return "".join(c if c.isalnum() else "_" for c in name)


def test_node_runs_every_case_as_the_table_says(tmp_path):
    node, hook = node_or_skip(tmp_path)
    wrong = []
    for name, spec in sorted(CASES.items()):
        repo = hook.parent / "repos" / slug(name)
        for rel, source in spec["files"].items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        out = run_node(node, hook, repo, spec["entry"], spec["args"])
        if out != spec["node"]:
            wrong.append((name, out))
    assert not wrong, wrong


# ---- 2. a block enum over a parameter, and the names an enum or an interface binds

BLOCK_OVER_PARAMETER = (
    "class K { m() {} }\n"
    "function g(NAME: K) {\n"
    "  if (NAME) {\n"
    "    DECL\n"
    "  }\n"
    "  NAME.m();\n"
    "}\n"
)
DECLARATIONS = {
    "enum": "enum NAME { A }",
    "const enum": "const enum NAME { A }",
    "interface": "interface NAME { x: 1 }",
    "type": "type NAME = number;",
}


@pytest.mark.parametrize("name", ["module", "exports", "require", "process"])
@pytest.mark.parametrize("kind", sorted(DECLARATIONS))
def test_a_block_declaration_named_like_a_module_root_does_not_hide_the_parameter_outside_the_block(make_repo, name, kind):
    source = BLOCK_OVER_PARAMETER.replace("DECL", DECLARATIONS[kind]).replace("NAME", name)
    graph = graph_of(make_repo({"main.ts": source}), "ts")
    assert ("main.ts", 6, "main:g", "main:K.m") in {(e.file, e.line, e.caller, e.callee) for e in graph.edges}, graph.unresolved


@pytest.mark.parametrize("name", ["module", "exports", "require", "process"])
@pytest.mark.parametrize("decl", ["interface NAME { x: 1 }", "type NAME = number;"])
def test_an_erased_declaration_in_a_function_is_never_the_reason_a_module_object_call_is_outside_the_repo(make_repo, name, decl):
    """The regression: an interface or a type alias named like a module root, inside a function, made `module.exports.f()` there a call of
    a builtin: it is not a value, so the call runs the module's own `f` (Node: HIT main:f)."""
    tail = {"module": "module.exports.f();", "exports": "exports.f();", "require": "require.main.exports.f();", "process": "process.mainModule.exports.f();"}
    source = SET + "function g() { " + decl.replace("NAME", name) + " " + tail[name] + " }\ng();\n"
    graph = graph_of(make_repo({"main.ts": source}), "ts")
    assert graph.external_calls == 0, (graph.external_calls, graph.external_reasons)
    assert any(OWN in u.reason for u in graph.unresolved if u.line == 3), graph.unresolved


# ---- 4. the reason a child of <exports.Wrap> has no renderer

N2 = (
    "import React from 'react';\n"
    "function Child() { return <i />; }\n"
    "export function App() {\n"
    "  return (\n"
    "    <exports.Wrap>\n"
    "      <Child />\n"
    "    </exports.Wrap>\n"
    "  );\n"
    "}\n"
)


def test_a_child_of_a_tag_through_exports_is_not_said_to_be_outside_the_repo(make_repo):
    graph = graph_of(make_repo({"main.jsx": N2}), "js")
    reasons = [u.reason for u in graph.unresolved if u.file == "main.jsx" and u.line == 6]
    assert len(reasons) == 1, reasons
    assert "<exports.Wrap> is not known to be a component of the repo" in reasons[0], reasons[0]
    assert "is not a component of the repo" not in reasons[0], reasons[0]
    assert "the component that renders it is not proven" in reasons[0]


def test_a_child_of_a_tag_through_the_global_object_is_not_said_to_be_outside_the_repo_either(make_repo):
    graph = graph_of(make_repo({"main.jsx": N2.replace("<exports.Wrap>", "<globalThis.module.exports.Wrap>").replace("</exports.Wrap>", "</globalThis.module.exports.Wrap>")}), "js")
    reasons = [u.reason for u in graph.unresolved if u.file == "main.jsx" and u.line == 6]
    assert len(reasons) == 1 and "is not known to be a component of the repo" in reasons[0], reasons
    assert "is not a component of the repo" not in reasons[0], reasons[0]


# ---- the scope of each kind of declaration, every row run in Node
#
# One module name (`module`, `exports`, `require`, `process`) is declared by each kind of declaration, written in each position (at the top
# of the file, in a block at the top, in a function, in a block of it, in a block inside that, in a switch case), with the call before it
# where that makes a different row, with an erased declaration of the same name beside it (an interface, or a namespace of types only) and
# without. Node says what each call runs: the repo's value (`LOCAL`), the module's own object (`main:f`) or an error. The answer must be
#   - the module's own, unresolved with that reason, where Node runs `main:f` (nothing of that name is a value there);
#   - never outside the repo, and never a member of the module's own objects, where Node does not: the name is a value there (a `var` and a
#     sloppy-mode function declaration reach past their block, a `let`, `const`, `class`, `enum` and `namespace` stop at it, an erased
#     declaration beside them changes nothing). At the top of a CommonJS file the reason is left as it was (the module's own, not known to be
#     outside the repo): a top-level `var`, `enum` or `namespace` of a name merges with or redeclares what Node runs the file with, and which
#     the call reaches is not known.
# The scope table STATEMENT_KINDS in ripple/ts_indexer.py states the same per kind.

import collections  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

PATHS = {"module": ["exports", "f"], "exports": ["f"], "require": ["main", "exports", "f"], "process": ["mainModule", "exports", "f"]}
FN = "{ Math.HIT = 'LOCAL'; }"


def obj(path):
    """A literal whose last step is a method that records LOCAL: `{ exports: { f() {...} } }`."""
    return "{ %s() %s }" % (path[0], FN) if len(path) == 1 else "{ %s: %s }" % (path[0], obj(path[1:]))


def call(name):
    return name + "".join("." + p for p in PATHS[name]) + "()"


def member(path, kind):
    if len(path) == 1:
        return "static f() %s" % FN if kind == "class" else "export function f() %s" % FN
    return ("static %s = %s;" if kind == "class" else "export const %s = %s;") % (path[0], obj(path[1:]))


def declaration(kind, name):
    """(source that declares `name` as a repo value of the kind, extra Node args)."""
    path = PATHS[name]
    if kind in ("var", "let", "const"):
        return f"{kind} {name}: any = {obj(path)};", []
    if kind == "function":
        return f"function {name}() {{}}\n{name}.{path[0]} = {obj(path[1:]) if len(path) > 1 else 'function () ' + FN};", []
    if kind == "class":
        return f"class {name} {{ {member(path, 'class')} }}", []
    if kind in ("enum", "const enum"):
        return f"{kind} {name} {{ A }}", TRANSFORM
    if kind == "module keyword":  # `module N {}` is the grammar's own kind of node, not a namespace's
        return f"module {name} {{ {member(path, 'namespace')} }}", TRANSFORM
    return f"namespace {name} {{ {member(path, 'namespace')} }}", TRANSFORM


BESIDE = {"": "", "interface": "interface NAME { x: 1 }\n", "types-only namespace": "namespace NAME { export interface I { x: 1 } }\n"}
KINDS = ["var", "let", "const", "function", "class", "enum", "const enum", "namespace", "module keyword"]
LAYOUTS = {
    "top": "@E@@D@\n@C@\n",
    "top block, call inside": "{\n@E@@D@\n@C@\n}\n",
    "top block, call after": "{\n@E@@D@\n}\n@C@\n",
    "function": "function g() {\n@E@@D@\n@C@\n}\ng();\n",
    "function, call before": "function g() {\n@C@\n@E@@D@\n}\ng();\n",
    "block, call inside": "function g() {\n{\n@E@@D@\n@C@\n}\n}\ng();\n",
    "block, call after": "function g() {\n{\n@E@@D@\n}\n@C@\n}\ng();\n",
    "nested block, call inside": "function g() {\n{\n@E@@D@\n{\n@C@\n}\n}\n}\ng();\n",
    "nested block, call after": "function g() {\n{\n{\n@E@@D@\n}\n@C@\n}\n}\ng();\n",
    "switch case": "function g() {\nswitch (1) { case 1:\n@E@@D@\n@C@\nbreak; }\n}\ng();\n",
    # an anonymous function has no frame of its own in the function it stands in: its names are keyed apart
    "anonymous function, block, call after": "(() => {\n{\n@E@@D@\n}\n@C@\n})();\n",
    "anonymous function, call inside": "(() => {\n@E@@D@\n@C@\n})();\n",
}


def scope_cells():
    """[(id, name, kind, position, source, node args)]. exports and require are tried with the kinds that differ, as they only rename."""
    out = []
    for name in PATHS:
        for kind in KINDS if name in ("module", "process") else ["var", "function", "class", "namespace"]:
            decl, args = declaration(kind, name)
            for position, layout in LAYOUTS.items():
                if position.startswith("top") and kind in ("let", "const", "class") and name != "process":
                    continue  # `module`, `exports` and `require` are the parameters of a CommonJS file: a top-level let, const or class of one is a SyntaxError
                for beside, erased in BESIDE.items():
                    if beside and kind in ("enum", "const enum"):
                        continue  # an enum is a type and a value: an interface of the name beside it is a duplicate declaration
                    source = layout.replace("@E@", erased.replace("NAME", name)).replace("@D@", decl).replace("@C@", call(name) + "; // CALL")
                    out.append((f"{name}|{kind}|{position}|{beside or '-'}", name, kind, position, SET + source, args))
    return out


# Every kind of declaration beside every other kind of the same name, in the same block or function (the "twin" axis): JavaScript makes most
# pairs a SyntaxError (a `let` and a `var`, two `let`s), TypeScript's own checker rejects others (a `var` and a `namespace`), and Node's
# type transform runs some of them. Node says which, for each pair and in each order; the answer must be what that row of the matrix says.
TWIN_LAYOUTS = {
    "function": "function g(@H@) {\n@D@\n@C@\n}\ng(@ARG@);\n",
    "block, call inside": "function g(@H@) {\n{\n@D@\n@C@\n}\n}\ng(@ARG@);\n",
    "block, call after": "function g(@H@) {\n{\n@D@\n}\n@C@\n}\ng(@ARG@);\n",
    "anonymous function, block, call after": "((@H@) => {\n{\n@D@\n}\n@C@\n})(@ARG@);\n",
}


def twin_cells():
    """[(id, name, "A+B", position, source, node args)]: B declared after A, A being a parameter of the function in some rows."""
    out = []
    for name in PATHS:
        kinds = KINDS if name in ("module", "process") else ["var", "function", "class", "namespace"]
        for first in ["parameter", *kinds]:
            for second in kinds:
                declared, args = declaration(second, name)
                if first != "parameter":
                    earlier, earlier_args = declaration(first, name)
                    declared, args = earlier + "\n" + declared, sorted(set(args) | set(earlier_args))
                for position, layout in TWIN_LAYOUTS.items():
                    head, argument = (f"{name}: any", obj(PATHS[name])) if first == "parameter" else ("", "")
                    source = layout.replace("@H@", head).replace("@ARG@", argument).replace("@D@", declared).replace("@C@", call(name) + "; // CALL")
                    out.append((f"{name}|{first}+{second}|{position}|-", name, f"{first}+{second}", position, SET + source, args))
    return out


SCOPE_CELLS = scope_cells() + twin_cells()


def judge_scope_cell(graph, cid, source, node_out):
    """What is wrong with ripple's answer to the row, or ''."""
    name, kind, position, _beside = cid.split("|")
    site = next(n for n, line in enumerate(source.splitlines(), 1) if line.rstrip().endswith("// CALL"))
    reasons = " ".join(u.reason for u in graph.unresolved if (u.file, u.line) == ("main.cts", site))
    edges = [e for e in graph.edges if (e.file, e.line) == ("main.cts", site)]
    if graph.external_calls:
        return f"counted as outside the repo ({graph.external_reasons})"
    if node_out.startswith("THROW SyntaxError"):  # Node rejects the pair (a redeclaration): no call runs, so only an edge or an outside call would be a claim
        # KNOWN-WRONG: ripple does not look for a redeclaration, so a class of the name that holds the function (`class exports { static f() }`)
        # keeps its edge beside a `var`, a function or another class of the name, for every name and not only these (main does the same)
        if edges and not ("class" in kind.split("+") and all(e.callee.endswith(".f") for e in edges)):
            return f"an edge {[e.callee for e in edges]} for a pair Node rejects"
        return ""
    if node_out == "RESULT main:f":
        return "" if OWN in reasons and not edges else f"Node runs the module's own, ripple says {reasons!r} {[e.callee for e in edges]}"
    # a class of the name has the function (`static f`): the edge is the call's (ripple does not model the temporal dead zone, so the call
    # before the class is drawn the same, as it is for any class)
    if edges and not ("class" in kind.split("+") and all(e.callee.endswith(".f") for e in edges)):
        return f"an edge {[e.callee for e in edges]} where Node does not run the module's own and the declaration is not a class"
    if (OWN in reasons or GLOBAL in reasons) and not position.startswith("top"):
        return f"the name is a value here (Node: {node_out}) and the answer says it is the module's own: {reasons!r}"
    return ""



def test_the_scope_matrix_is_what_it_says_it_is():
    counts = collections.Counter((kind, position) for _cid, _name, kind, position, _source, _args in SCOPE_CELLS)
    assert len(SCOPE_CELLS) > 1000 and all(count >= 2 for count in counts.values()), counts
    assert {kind for kind, _ in counts if "+" not in kind} == set(KINDS)
    assert {position for kind, position in counts if "+" not in kind} == set(LAYOUTS)
    pairs = {tuple(kind.split("+")) for kind, _ in counts if "+" in kind}
    assert {(a, b) for a in KINDS for b in KINDS} <= pairs  # every kind beside every other kind (and itself)


def test_ripple_answers_every_scope_cell_as_node_runs_it(tmp_path):
    node, hook = node_or_skip(tmp_path)

    def run(cell):
        cid, _name, _kind, _position, source, args = cell
        repo = hook.parent / "scope" / f"{SCOPE_CELLS.index(cell)}"
        repo.mkdir(parents=True)
        (repo / "main.cts").write_text(source)
        return cid, source, repo, run_node(node, hook, repo, "main.cts", args)

    with ThreadPoolExecutor(max_workers=4) as pool:
        ran = list(pool.map(run, SCOPE_CELLS))
    wrong = []
    for cid, source, repo, node_out in ran:
        assert node_out.startswith(("RESULT", "THROW")) and not node_out.startswith("NOOUT"), (cid, node_out)
        why = judge_scope_cell(build_graph(repo, TYPESCRIPT), cid, source, node_out)
        if why:
            wrong.append((cid, node_out, why))
    assert not wrong, wrong[:12]


PARAMETER_ROWS = {
    # a parameter is a value of the whole function: visible in it, an erased declaration beside it changes nothing; a `var` of the same name
    # is the parameter again (Node: LOCAL), and a catch binding belongs to the catch block (after it the call is the module's own)
    "parameter": ("function g(NAME: any) {\n@E@@C@\n}\ng(@V@);\n", "RESULT LOCAL"),
    "catch binding": ("function g() {\ntry { throw @V@; } catch (NAME: any) {\n@E@@C@\n}\n}\ng();\n", "RESULT LOCAL"),
    "after a catch": ("function g() {\ntry { throw @V@; } catch (NAME: any) {\n@E@\n}\n@C@\n}\ng();\n", "RESULT main:f"),
}


@pytest.mark.parametrize("beside", ["", "interface", "types-only namespace"])
@pytest.mark.parametrize("row", sorted(PARAMETER_ROWS))
@pytest.mark.parametrize("name", sorted(PATHS))
def test_a_parameter_and_a_catch_binding_are_values_of_their_own_scope(tmp_path, name, row, beside):
    node, hook = node_or_skip(tmp_path)
    layout, expected = PARAMETER_ROWS[row]
    source = SET + layout.replace("NAME", name).replace("@E@", BESIDE[beside].replace("NAME", name)).replace("@V@", obj(PATHS[name])).replace("@C@", call(name) + "; // CALL")
    repo = hook.parent / "param" / slug(f"{name}-{row}-{beside}")
    repo.mkdir(parents=True)
    (repo / "main.cts").write_text(source)
    node_out = run_node(node, hook, repo, "main.cts", [])
    assert node_out == expected, node_out
    assert judge_scope_cell(build_graph(repo, TYPESCRIPT), f"{name}|{row}|function|-", source, node_out) == ""


# ---- the reviewer's cases, each as written (every one answered unresolved by main, and external by the tip this change builds on)

REVIEW_CASES = {
    "var beside an interface in a block, process.cwd() after it": (
        "function g() {\n{ interface process { x: 1 } var process: any = { cwd() { Math.HIT = 'LOCAL'; } }; }\nprocess.cwd(); // CALL\n}\ng();\n",
        "RESULT LOCAL",
    ),
    "var beside an interface in a block, module.exports.f() after it": (
        "function g() {\n{ interface module { x: 1 } var module: any = { exports: { f() { Math.HIT = 'LOCAL'; } } }; }\nmodule.exports.f(); // CALL\n}\ng();\n",
        "RESULT LOCAL",
    ),
    "var beside a type alias in a block, process.mainModule": (
        "function g() {\n{ type process = number; var process: any = { mainModule: { exports: { f() { Math.HIT = 'LOCAL'; } } } }; }\nprocess.mainModule.exports.f(); // CALL\n}\ng();\n",
        "RESULT LOCAL",
    ),
    "var beside an interface in a block, require.main": (
        "function g() {\n{ interface require { x: 1 } var require: any = { main: { exports: { f() { Math.HIT = 'LOCAL'; } } } }; }\nrequire.main.exports.f(); // CALL\n}\ng();\n",
        "RESULT LOCAL",
    ),
    "the same at the top of the file": (
        "{ interface process { x: 1 } var process: any = { cwd() { Math.HIT = 'LOCAL'; } }; }\nprocess.cwd(); // CALL\n",
        "RESULT LOCAL",
    ),
    "var beside an interface, in a loop head": (
        "function g() {\nfor (var process: any = { cwd() { Math.HIT = 'LOCAL'; } }; ; ) { interface process { x: 1 } break; }\nprocess.cwd(); // CALL\n}\ng();\n",
        "RESULT LOCAL",
    ),
    "an assignment beside an interface in a block": (
        "function g() {\nlet process: any = { cwd() { Math.HIT = 'LOCAL'; } };\n{ interface process { x: 1 } process = { cwd() { Math.HIT = 'LOCAL2'; } }; }\nprocess.cwd(); // CALL\n}\ng();\n",
        "RESULT LOCAL2",
    ),
    "a var after the call, in the same function (hoisted: undefined)": (
        "function g() {\nprocess.cwd(); // CALL\nvar process: any = { cwd() { Math.HIT = 'LOCAL'; } };\n}\ng();\n",
        "THROW TypeError RESULT NOHIT",
    ),
    "a var after the call, in a block of the same function": (
        "function g() {\nmodule.exports.f(); // CALL\n{ var module: any = { exports: { f() { Math.HIT = 'LOCAL'; } } }; }\n}\ng();\n",
        "THROW TypeError RESULT NOHIT",
    ),
    "a namespace beside a parameter of the same name (MED)": (
        "class K { m() { Math.HIT = 'main:K.m'; } }\nfunction g(module: K) {\nnamespace module { export function m() { Math.HIT = 'ns.m'; } }\nmodule.m(); // CALL\n}\ng(new K());\n",
        "RESULT ns.m",
    ),
    "a namespace process in a function, process.cwd() (MED)": (
        "function g() {\nnamespace process { export function cwd() { Math.HIT = 'ns.cwd'; } }\nprocess.cwd(); // CALL\n}\ng();\n",
        "RESULT ns.cwd",
    ),
    "a namespace process in a block, in a nested function, over a parameter": (
        "function g(process: any) {\nfunction h() {\nnamespace process { export function cwd() { Math.HIT = 'ns.cwd'; } }\nprocess.cwd(); // CALL\n}\nh();\n}\ng({ cwd() { Math.HIT = 'LOCAL'; } });\n",
        "RESULT ns.cwd",
    ),
    "export namespace in a namespace body": (
        "namespace outer {\nexport namespace process { export function cwd() { Math.HIT = 'ns.cwd'; } }\nexport function run() { process.cwd(); } // CALL\n}\nouter.run();\n",
        "RESULT ns.cwd",
    ),
}


@pytest.mark.parametrize("name", sorted(REVIEW_CASES))
def test_the_review_cases_run_in_node_as_stated_and_are_never_answered_outside_the_repo(tmp_path, make_repo, name):
    source, expected = REVIEW_CASES[name]
    node, hook = node_or_skip(tmp_path)
    nodedir = hook.parent / "review" / slug(name)
    nodedir.mkdir(parents=True)
    (nodedir / "main.cts").write_text(SET + source)
    assert run_node(node, hook, nodedir, "main.cts", TRANSFORM) == expected
    graph = graph_of(make_repo({"main.cts": SET + source}), "ts")
    line = next(n for n, text in enumerate((SET + source).splitlines(), 1) if text.rstrip().endswith("// CALL"))
    assert graph.external_calls == 0, graph.external_reasons
    assert not [e for e in graph.edges if (e.file, e.line) == ("main.cts", line)], graph.edges
    assert not any(OWN in u.reason or GLOBAL in u.reason for u in graph.unresolved if u.line == line), graph.unresolved


# ---- every statement-level kind of the grammar is classified, and `declaration_of` sees through every wrapper

DECLARATION_SNIPPETS = [
    # (source, the node `declaration_of` returns, the name it binds, whether it follows `declare`)
    ("namespace N { export const x = 1 }", "internal_module", "N", False),
    ("module N { export const x = 1 }", "module", "N", False),
    ("namespace A.B { export const x = 1 }", "internal_module", "A", False),
    ("export namespace N { export const x = 1 }", "internal_module", "N", False),
    ("declare namespace N { const x: 1 }", "internal_module", "N", True),
    ("export declare namespace N { const x: 1 }", "internal_module", "N", True),
    ("declare module 'm' { const x: 1 }", "module", None, True),
    ("declare global { interface Window { x: 1 } }", "statement_block", None, True),
    ("declare const x: 1;", "lexical_declaration", None, True),
    ("declare function f(): void;", "function_signature", "f", True),
    ("export import a = N.M;", "import_alias", None, False),
    ("declare class C {}", "class_declaration", "C", True),
    ("declare enum E { A }", "enum_declaration", "E", True),
    ("export declare const enum E { A }", "enum_declaration", "E", True),
    ("abstract class C {}", "abstract_class_declaration", "C", False),
    ("export abstract class C {}", "abstract_class_declaration", "C", False),
    ("export default abstract class C {}", "abstract_class_declaration", "C", False),
    ("export default class C {}", "class_declaration", "C", False),
    ("export default function f() {}", "function_declaration", "f", False),
    ("export default interface I {}", "interface_declaration", "I", False),
    ("@d class C {}", "class_declaration", "C", False),
    ("@d export class C {}", "class_declaration", "C", False),
    ("export @d class C {}", "class_declaration", "C", False),
    ("l: function f() {}", "function_declaration", "f", False),
    ("l: m: function f() {}", "function_declaration", "f", False),
    ("enum E { A }", "enum_declaration", "E", False),
    ("const enum E { A }", "enum_declaration", "E", False),
    ("export const enum E { A }", "enum_declaration", "E", False),
    ("interface I { x: 1 }", "interface_declaration", "I", False),
    ("type T = 1;", "type_alias_declaration", "T", False),
    ("export type T = 1;", "type_alias_declaration", "T", False),
    ("async function f() {}", "function_declaration", "f", False),
    ("function* f() {}", "generator_function_declaration", "f", False),
    ("export async function f() {}", "function_declaration", "f", False),
    ("class C {}", "class_declaration", "C", False),
    ("let x = 1;", "lexical_declaration", None, False),
    ("var x = 1;", "variable_declaration", None, False),
]
# statements that hold no declaration, and the kind `declaration_of` leaves them as
OTHER_SNIPPETS = {
    "export default 1;": "export_statement",
    "export = x;": "export_statement",
    "export { a };": "export_statement",
    "export * from 'a';": "export_statement",
    "export default class {}": "export_statement",
    "export default function () {}": "export_statement",
    "x;": "expression_statement",
    "x = 1;": "expression_statement",
    "import 'a';": "import_statement",
    "import a = require('a');": "import_statement",
    "import a = N.M;": "import_alias",
    "if (1) { }": "if_statement",
    "for (;;) { break; }": "for_statement",
    "for (const x of y) { }": "for_in_statement",
    "while (0) { }": "while_statement",
    "do { } while (0);": "do_statement",
    "try { } catch (e) { }": "try_statement",
    "with (a) { }": "with_statement",
    "switch (1) { }": "switch_statement",
    "{ }": "statement_block",
    ";": "empty_statement",
    "debugger;": "debugger_statement",
    "l: { }": "statement_block",
}


WHERES = ["top", "function", "block", "switch", "namespace"]


def parse_statements(source, where):
    """The statement nodes of `source` written at top level, in a function, in a block, in a case of a switch or in a namespace body."""
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    parser = Parser(Language(tree_sitter_typescript.language_typescript()))
    wrapped = {
        "top": "{s}",
        "function": "function g() {{ {s} }}",
        "block": "function g() {{ {{ ; {s} }} }}",  # the empty statement keeps the block from being read as an object literal
        "switch": "function g() {{ switch (1) {{ case 1: {s} }} }}",
        "namespace": "namespace Z {{ {s} }}",
    }[where].format(s=source)
    root = parser.parse(wrapped.encode()).root_node
    if where == "top":
        holder, skip = root, 0
    elif where == "function":
        holder, skip = root.named_children[0].child_by_field_name("body"), 0
    elif where == "block":
        holder, skip = root.named_children[0].child_by_field_name("body").named_children[0], 1
    elif where == "switch":  # the first named child of a case is its value
        case = root.named_children[0].child_by_field_name("body").named_children[0].child_by_field_name("body").named_children[0]
        holder, skip = case, 1
    else:  # the grammar holds a namespace in an expression statement
        holder, skip = root.named_children[0].named_children[0].child_by_field_name("body"), 0
    return [c for c in holder.named_children if c.type != "comment"][skip:]


@pytest.mark.parametrize("where", WHERES)
@pytest.mark.parametrize("snippet", [row[0] for row in DECLARATION_SNIPPETS])
def test_a_declaration_is_seen_through_every_wrapper_the_grammar_holds_it_in(snippet, where):
    from ripple.ts_indexer import declared_name, declaration_of

    (kind, name, ambient) = next(row[1:] for row in DECLARATION_SNIPPETS if row[0] == snippet)
    statements = parse_statements(snippet, where)
    assert len(statements) == 1, [s.type for s in statements]
    held, follows_declare = declaration_of(statements[0])
    assert (held.type, declared_name(held), follows_declare) == (kind, name, ambient), (held.type, declared_name(held), follows_declare)


@pytest.mark.parametrize("where", WHERES)
@pytest.mark.parametrize("snippet", sorted(OTHER_SNIPPETS))
def test_a_statement_that_holds_no_declaration_is_left_as_it_is(snippet, where):
    from ripple.ts_indexer import declaration_of

    statements = parse_statements(snippet, where)
    assert len(statements) == 1, [s.type for s in statements]
    held, follows_declare = declaration_of(statements[0])
    assert (held.type, follows_declare) == (OTHER_SNIPPETS[snippet], False)


def test_every_statement_level_kind_the_snippets_hold_is_classified():
    from ripple.ts_indexer import STATEMENT_KINDS, declaration_of

    seen = set()
    for source in [row[0] for row in DECLARATION_SNIPPETS] + list(OTHER_SNIPPETS):
        for where in WHERES:
            for statement in parse_statements(source, where):
                seen |= {statement.type, declaration_of(statement)[0].type}
    assert not [kind for kind in seen if kind not in STATEMENT_KINDS], sorted(kind for kind in seen if kind not in STATEMENT_KINDS)


def test_every_statement_or_declaration_kind_of_the_grammar_is_classified():
    """A grammar release that adds a statement or a declaration the front end has not been told about fails here."""
    import tree_sitter_typescript
    from tree_sitter import Language

    from ripple.ts_indexer import STATEMENT_KINDS

    language = Language(tree_sitter_typescript.language_typescript())
    kinds = {language.node_kind_for_id(i) for i in range(language.node_kind_count) if language.node_kind_is_named(i) and language.node_kind_is_visible(i)}
    statements = {k for k in kinds if k.endswith(("_statement", "_declaration"))} | {"internal_module", "module", "import_alias", "function_signature"}
    assert not [k for k in sorted(statements) if k not in STATEMENT_KINDS], [k for k in sorted(statements) if k not in STATEMENT_KINDS]
    assert not [k for k in STATEMENT_KINDS if k not in kinds], [k for k in STATEMENT_KINDS if k not in kinds]


# The scope of each kind, as the scope matrix above shows it in Node. The code reads ripple.ts_indexer.STATEMENT_KINDS (declaration_of,
# _declared_names, _var_names, _bind_type_names, TYPE_DECLARATIONS, NAMESPACES), so a row changed there changes what ripple answers and
# the matrix fails; this literal fails first, for a row whose change no cell of the matrix could see (a statement that binds nothing).
EXPECTED_SCOPES = {
    "export_statement": "wrapper",
    "expression_statement": "wrapper",
    "labeled_statement": "wrapper",
    "ambient_declaration": "ambient",
    "lexical_declaration": "block",
    "variable_declaration": "function",
    "function_declaration": "block function",
    "generator_function_declaration": "block function",
    "class_declaration": "block",
    "abstract_class_declaration": "block",
    "enum_declaration": "block type",
    "internal_module": "namespace",
    "module": "namespace",
    "interface_declaration": "erased",
    "type_alias_declaration": "erased",
    "import_statement": "import",
    "import_alias": "none",
}


def test_the_scope_of_each_kind_of_declaration_is_the_one_the_matrix_shows():
    from ripple.ts_indexer import NAMESPACES, STATEMENT_KINDS, TYPE_DECLARATIONS

    assert {kind: STATEMENT_KINDS[kind] for kind in EXPECTED_SCOPES} == EXPECTED_SCOPES
    others = {kind: role for kind, role in STATEMENT_KINDS.items() if kind not in EXPECTED_SCOPES}
    assert set(others.values()) == {"none"}, others  # the control statements bind no name of their own
    assert set(NAMESPACES) == {"internal_module", "module"}
    assert set(TYPE_DECLARATIONS) == {"enum_declaration", "internal_module", "module", "interface_declaration", "type_alias_declaration"}


def test_every_wrapper_row_has_a_holder_and_every_holder_a_wrapper_row():
    from ripple.ts_indexer import HOLDERS, STATEMENT_KINDS

    assert {kind for kind, role in STATEMENT_KINDS.items() if role == "wrapper"} == set(HOLDERS)


# ---- the cases of the third review, each as written, with Node's output (and the answer of every kind that a block's `var` meets)

THIRD_REVIEW = {
    # (source, node args, what Node prints, the edge ripple must draw or None for an unresolved call that is never outside the repo)
    "a var and a value namespace in one block, then process.cwd() (MED-1, c15/c2)": (
        "function g() {\n{ var process: any = { cwd() { Math.HIT = 'LOCAL'; } }; namespace process { export const a = 1; } }\nprocess.cwd(); // CALL\n}\ng();\n",
        TRANSFORM,
        "RESULT LOCAL",
        None,
    ),
    "the same with a namespace first": (
        "function g() {\n{ namespace process { export const a = 1; } var process: any = { cwd() { Math.HIT = 'LOCAL'; } }; }\nprocess.cwd(); // CALL\n}\ng();\n",
        TRANSFORM,
        "RESULT LOCAL",
        None,
    ),
    "the same with module N {}": (
        "function g() {\n{ var process: any = { cwd() { Math.HIT = 'LOCAL'; } }; module process { export const a = 1; } }\nprocess.cwd(); // CALL\n}\ng();\n",
        TRANSFORM,
        "RESULT LOCAL",
        None,
    ),
    "the same in a switch case": (
        "function g() {\nswitch (1) { case 1: namespace process { export const a = 1; } var process: any = { cwd() { Math.HIT = 'LOCAL'; } }; }\nprocess.cwd(); // CALL\n}\ng();\n",
        TRANSFORM,
        "RESULT LOCAL",
        None,
    ),
    "the same at the top of the file, module.exports.f()": (
        "{ namespace module { export const a = 1; } var module: any = { exports: { f() { Math.HIT = 'LOCAL'; } } }; }\nmodule.exports.f(); // CALL\n",
        TRANSFORM,
        "RESULT LOCAL",
        None,
    ),
    "a var and a value namespace of an ordinary name: the var's class, not the outer one (MED-1, c15/c0)": (
        "class K { m() { Math.HIT = 'K.m'; } }\nclass J { m() { Math.HIT = 'J.m'; } }\nconst k = new J();\n"
        "function g() {\n{ var k = new K(); namespace k { export const a = 1; } }\nk.m(); // CALL\n}\ng();\n",
        TRANSFORM,
        "RESULT K.m",
        "main:K.m",
    ),
    "the same with module k {}": (
        "class K { m() { Math.HIT = 'K.m'; } }\nclass J { m() { Math.HIT = 'J.m'; } }\nconst k = new J();\n"
        "function g() {\n{ var k = new K(); module k { export const a = 1; } }\nk.m(); // CALL\n}\ng();\n",
        TRANSFORM,
        "RESULT K.m",
        "main:K.m",
    ),
    "a var function beside a namespace of the name": (
        "class K { m() { Math.HIT = 'K.m'; } }\nclass J { m() { Math.HIT = 'J.m'; } }\nconst k = new J();\n"
        "function g() {\n{ namespace k { export const a = 1; } var k = () => new K(); }\nk().m(); // CALL\n}\ng();\n",
        TRANSFORM,
        "RESULT K.m",
        "main:g.k",  # the call of k itself: k is the function the var holds
    ),
    "a var in a loop head beside a namespace": (
        "class K { m() { Math.HIT = 'K.m'; } }\nclass J { m() { Math.HIT = 'J.m'; } }\nconst k = new J();\n"
        "function g() {\n{ namespace k { export const a = 1; } for (var k = new K(); ; ) { break; } }\nk.m(); // CALL\n}\ng();\n",
        TRANSFORM,
        "RESULT K.m",
        "main:K.m",
    ),
    "a read beside an interface of the name reaches the var written there (LOW-2, c13/c2)": (
        "class K { cwd() { Math.HIT = 'K.cwd'; } m() { Math.HIT = 'K.m'; } }\n"
        "function g() {\n{ interface process { x: 1 } var process = new K();\nprocess.m(); } // CALL\n}\ng();\n",
        [],
        "RESULT K.m",
        "main:K.m",
    ),
    "a read beside an interface of the name reaches the parameter (LOW-2, c13/c3)": (
        "class K { m() { Math.HIT = 'K.m'; } }\nfunction g(module: K) {\n{ interface module { x: 1 }\nmodule.m(); } // CALL\n}\ng(new K());\n",
        [],
        "RESULT K.m",
        "main:K.m",
    ),
    "a var in a catch clause of the same name assigns the parameter (the one block a var reaches)": (
        "class K { m() { Math.HIT = 'K.m'; } }\nclass J { m() { Math.HIT = 'J.m'; } }\n"
        "function g() {\ntry { throw new J(); } catch (e) {\nvar e = new K();\ne.m(); // CALL\n}\n}\ng();\n",
        [],
        "RESULT K.m",
        None,  # the parameter holds a caught exception and the var's class: not known which (no edge, and never outside the repo)
    ),
    "a var in a catch clause is not the function's: after the clause the name is still undefined": (
        "class K { m() { Math.HIT = 'K.m'; } }\nclass J { m() { Math.HIT = 'J.m'; } }\n"
        "function g() {\ntry { throw new J(); } catch (e) {\nvar e = new K();\n}\ne.m(); // CALL\n}\ng();\n",
        [],
        "THROW TypeError RESULT NOHIT",
        None,
    ),
    "a var loop head beside a namespace of the name, over an outer name": (
        "class J { m() { Math.HIT = 'J.m'; } }\nconst k = new J();\n"
        "function g() {\n{ namespace k { export const a = 1; } for (var k in { a: 1 }) { break; } }\nk.m(); // CALL\n}\ng();\n",
        TRANSFORM,
        "THROW TypeError RESULT NOHIT",
        None,
    ),
    "a write beside an interface of the name is the write the later read sees": (
        "class J { cwd() { Math.HIT = 'J.cwd'; } }\nclass K { cwd() { Math.HIT = 'K.cwd'; } }\n"
        "function g() {\nlet process = new J();\n{ interface process { x: 1 } process = new K(); }\nprocess.cwd(); // CALL\n}\ng();\n",
        [],
        "RESULT K.cwd",
        None,  # J and K are both assigned, so which one the call reaches is not known (never outside the repo, never the module's own)
    ),
    "a var in an anonymous function, beside an interface, over the outer name": (
        "class J { cwd() { Math.HIT = 'J.cwd'; } }\nclass K { cwd() { Math.HIT = 'K.cwd'; } }\nconst process = new J();\n"
        "(() => {\n{ interface process { x: 1 } var process = new K(); }\nprocess.cwd(); // CALL\n})();\n",
        [],
        "RESULT K.cwd",
        "main:K.cwd",
    ),
    "the builtin beside an interface of the name is the builtin (LOW-2, c13/c0)": (
        "function g() {\n{ interface process { x: 1 }\nprocess.cwd(); } // CALL\n}\ng();\n",
        [],
        "RESULT NOHIT",
        "EXTERNAL",
    ),
}


@pytest.mark.parametrize("name", sorted(THIRD_REVIEW))
def test_the_third_review_cases_run_in_node_as_stated_and_ripple_answers_them(tmp_path, make_repo, name):
    source, args, expected, want = THIRD_REVIEW[name]
    node, hook = node_or_skip(tmp_path)
    full = SET + source
    nodedir = hook.parent / "third" / slug(name)
    nodedir.mkdir(parents=True)
    (nodedir / "main.cts").write_text(full)
    assert run_node(node, hook, nodedir, "main.cts", args) == expected
    graph = graph_of(make_repo({"main.cts": full}), "ts")
    line = next(n for n, text in enumerate(full.splitlines(), 1) if text.rstrip().endswith("// CALL"))
    at_site = {e.callee for e in graph.edges if (e.file, e.line) == ("main.cts", line)}
    if want == "EXTERNAL":
        assert graph.external_calls == 1 and not at_site, (graph.external_reasons, at_site)
        return
    assert graph.external_calls == 0, graph.external_reasons
    assert at_site == ({want} if want else set()), (at_site, [u.reason for u in graph.unresolved if u.line == line])
    if want is None:
        assert any(u.line == line for u in graph.unresolved)
        assert "top of the file" in name or not any(OWN in u.reason or GLOBAL in u.reason for u in graph.unresolved if u.line == line), graph.unresolved


# ---- `import a = N.M` binds a name that ripple does not read, and is answered as outside the repo (KNOWN-WRONG)


@pytest.mark.xfail(strict=True, reason="KNOWN-WRONG: `import a = N.M` binds nothing ripple reads, so a call through it is a call of a name that is not declared: counted outside the repo")
@pytest.mark.parametrize(
    "source",
    [
        "namespace N { export namespace P { export function cwd() { Math.HIT = 'N.P.cwd'; } } }\nimport process = N.P;\nprocess.cwd(); // CALL\n",
        "namespace N { export namespace P { export function cwd() { Math.HIT = 'N.P.cwd'; } } }\nnamespace Z {\n  import process = N.P;\n  process.cwd(); // CALL\n}\n",
    ],
)
def test_a_call_through_an_import_alias_of_a_module_name_is_not_outside_the_repo(tmp_path, make_repo, source):
    node, hook = node_or_skip(tmp_path)
    full = SET + source
    nodedir = hook.parent / "alias" / str(abs(hash(source)))
    nodedir.mkdir(parents=True)
    (nodedir / "main.cts").write_text(full)
    assert run_node(node, hook, nodedir, "main.cts", TRANSFORM) == "RESULT N.P.cwd"  # Node runs repo code
    graph = graph_of(make_repo({"main.cts": full}), "ts")
    assert graph.external_calls == 0, graph.external_reasons


# ---- what the process import rule leaves alone


def test_a_repo_file_named_process_is_not_the_builtin(make_repo):
    # './process.js' is the repo's file: its `mainModule` is whatever it exports, so the call is not read as one through the entry module
    files = {
        "process.js": "export const mainModule = { exports: { f() {} } };\n",
        "b.mjs": "import { mainModule } from './process.js';\nmainModule.exports.f();\n",
    }
    graph = graph_of(make_repo(files), "js")
    assert OWN not in " ".join(u.reason for u in graph.unresolved if u.file == "b.mjs")
    assert graph.external_calls == 0


def test_a_child_of_a_package_component_keeps_its_reason(make_repo):
    source = "import React from 'react';\nimport { Wrap } from 'ui-kit';\nfunction Child() { return <i />; }\nexport const App = () => <Wrap><Child /></Wrap>;\n"
    graph = graph_of(make_repo({"main.jsx": source}), "js")
    reasons = [u.reason for u in graph.unresolved if u.file == "main.jsx" and u.line == 4 and u.text == "Child"]
    assert reasons == ["the component that renders it is not proven: <Wrap> is not a component of the repo"], reasons
