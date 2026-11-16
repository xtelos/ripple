"""Materialise the CommonJS probe repos under tests/fixtures/js_probes/cjs from the table below.

    python make_cjs_probes.py [OUT]        (OUT is the cjs directory beside this file by default)

A probe is a small repo of CommonJS files, each function of which returns its own id (`lib/a:helper`, the id ripple gives it).
Every call site the probe asks about is a line that ends in `// site sN` and holds exactly one call, inside a function the file
exports as `sN`. capture_cjs_expected.mjs runs node on the probe, calls every site, and writes expected.json beside it: what the
call really reached (the id a function returned), or that it threw. The test (test_js_commonjs_probes.py) needs no node: ripple
may be less certain than node, never more. PROBE.json keys besides notes:
  entries  the files node loads, in order (a .mjs is imported, anything else is required); the sites are read from all of them
  edge     [site, ...] sites ripple must draw an edge for (node reached a repo function, and the exports are provably closed)
  refuse   {site: part of the reason} sites ripple must leave unresolved, with a reason that says so
A probe may also hold symbolic links (probe(..., links={"lib/b.js": "a.js"}): the link, relative to its own directory), and a literal
absolute path must be /tmp/ripple-cjs-probes/<probe name>/..., where capture_cjs_expected.mjs runs the probe. A call that runs while its
file loads is written by the file as the object it exports as sN (`{ reached: id }` or `{ threw: name }`, see capture_cjs_child.mjs).
The maker deletes each probe directory it writes, expected.json included: run capture_cjs_expected.mjs after it.
"""
import json
import shutil
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "cjs"
PROBES = {}

A = "function helper() { return 'lib/a:helper'; }\n"
X = "function other() { return 'lib/x:other'; }\nmodule.exports = { other };\n"
MAIN_A = "const a = require('./lib/a');\n"


def probe(name, files, entries=("main.js",), edge=(), refuse=None, notes="", links=None):
    PROBES[name] = dict(files=files, entries=list(entries), edge=list(edge), refuse=refuse or {}, notes=notes, links=links or {})


def s(n, expr, indent=""):
    """The site function sN: `exports.sN = function () { return EXPR; }; // site sN`."""
    return f"exports.s{n} = function () {{ return {expr}; }}; // site s{n}\n"


def esm_s(n, expr):
    return f"export function s{n}() {{ return {expr}; }} // site s{n}\n"


def main(*lines):
    return "".join(lines)


M = "main.js"
# ---- the shapes that give a closed, provable export ------------------------------------------------------------------
probe("closed-object-namespace", {"lib/a.js": A + "module.exports = { helper };\n", M: MAIN_A + s(1, "a.helper()")},
      edge=[f"{M}:s1"], notes="module.exports = { helper }, called through the namespace binding.")
probe("closed-object-destructure", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "const { helper } = require('./lib/a');\nconst { helper: renamed } = require('./lib/a');\n" + s(1, "helper()") + s(2, "renamed()")},
      edge=[f"{M}:s1", f"{M}:s2"], notes="const { helper } and { helper: renamed } bind members of the closed exports.")
probe("closed-object-inline-member", {"lib/a.js": A + "module.exports = { helper };\n", M: s(1, "require('./lib/a').helper()")},
      edge=[f"{M}:s1"], notes="require('./x').f() is a member call of the target.")
probe("closed-exports-dot", {"lib/a.js": A + "exports.helper = helper;\n", M: MAIN_A + s(1, "a.helper()")},
      edge=[f"{M}:s1"], notes="exports.helper = helper.")
probe("closed-module-exports-dot", {"lib/a.js": A + "module.exports.helper = helper;\n", M: MAIN_A + s(1, "a.helper()")},
      edge=[f"{M}:s1"], notes="module.exports.helper = helper.")
probe("closed-function-export", {"lib/f.js": "function f() { return 'lib/f:f'; }\nmodule.exports = f;\n", M: "const f = require('./lib/f');\n" + s(1, "f()")},
      edge=[f"{M}:s1"], notes="module.exports = f: the whole export is the function.")
probe("closed-alias-function-with-props", {"lib/f.js": "function f() { return 'lib/f:f'; }\nfunction g() { return 'lib/f:g'; }\nexports = module.exports = f;\nexports.g = g;\n",
      M: "const f = require('./lib/f');\n" + s(1, "f()") + s(2, "f.g()")},
      edge=[f"{M}:s1"], notes="exports = module.exports = f, then a property of the function: f() is f, f.g() is not followed.")
probe("closed-class-export", {"lib/c.js": "class C {\n  constructor() { this.id = 'lib/c:C.constructor'; }\n  static make() { return 'lib/c:C.make'; }\n}\nmodule.exports = C;\n",
      M: "const C = require('./lib/c');\n" + s(1, "new C().id") + s(2, "C.make()")},
      refuse={f"{M}:s2": "class"}, notes="module.exports = a class: no member edge through a class a CommonJS file exports.")
probe("closed-reexport", {"lib/express.js": "function createApplication() { return 'lib/express:createApplication'; }\nexports = module.exports = createApplication;\n",
      "index.js": "module.exports = require('./lib/express');\n", M: "const express = require('./index');\n" + s(1, "express()")},
      edge=[f"{M}:s1"], notes="module.exports = require('./lib/express'), as express/index.js does.")
probe("closed-directory-index", {"lib/index.js": "function helper() { return 'lib:helper'; }\nmodule.exports = { helper };\n", M: "const lib = require('./lib');\n" + s(1, "lib.helper()")},
      edge=[f"{M}:s1"], notes="a directory with an index.js and no package.json.")
probe("closed-explicit-extension", {"lib/a.js": A + "module.exports = { helper };\n", "lib/c.cjs": "function viaC() { return 'lib/c:viaC'; }\nmodule.exports = { viaC };\n",
      M: "const a = require('./lib/a.js');\nconst c = require('./lib/c.cjs');\n" + s(1, "a.helper()") + s(2, "c.viaC()")},
      edge=[f"{M}:s1", f"{M}:s2"], notes="a written .js and a written .cjs.")
probe("closed-file-beats-directory", {"lib.js": "function pick() { return 'lib:pick'; }\nmodule.exports = { pick };\n", "lib/index.js": "function pick() { return 'lib/index:pick'; }\nmodule.exports = { pick };\n",
      M: "const lib = require('./lib');\n" + s(1, "lib.pick()")},
      notes="./lib is lib.js, not lib/index.js (Node tries the file first); the two fold to one module id, so ripple may refuse.")
probe("closed-arrow-and-alias-key", {"lib/a.js": "const helper = () => 'lib/a:helper';\nconst second = () => 'lib/a:second';\nmodule.exports = { helper, renamed: second };\n",
      M: MAIN_A + s(1, "a.helper()") + s(2, "a.renamed()")},
      edge=[f"{M}:s1", f"{M}:s2"], notes="const arrow functions, and a key that renames one.")
probe("closed-hoisted-function", {"lib/a.js": "module.exports = { helper };\nfunction helper() { return 'lib/a:helper'; }\n", M: MAIN_A + s(1, "a.helper()")},
      edge=[f"{M}:s1"], notes="the function is declared after the assignment: hoisted.")
probe("closed-require-in-function", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "exports.s1 = function () {\n  const a = require('./lib/a');\n  return a.helper(); // site s1\n};\n"},
      edge=[f"{M}:s1"], notes="require inside a function: the module cache gives the same exports.")
probe("closed-require-in-block", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "exports.s1 = function () {\n  if (true) {\n    const a = require('./lib/a');\n    return a.helper(); // site s1\n  }\n};\n"},
      edge=[f"{M}:s1"], notes="require inside a block, bound by const there.")
probe("closed-reexport-of-a-member", {"lib/a.js": A + "module.exports = { helper };\n", "lib/b.js": "const { helper } = require('./a');\nexports.helper = helper;\n",
      M: "const b = require('./lib/b');\n" + s(1, "b.helper()")},
      refuse={f"{M}:s1": "this"}, notes="exports.helper = a name destructured from another file: the call reaches a's helper, but a function written in another file may use `this` (the exports of b when called as b.helper()), so b is open.")
probe("closed-require-resolve", {"lib/a.js": A + "module.exports = { helper };\n",
      M: MAIN_A + "const where = require.resolve('./lib/a');\n" + s(1, "a.helper()")},
      edge=[f"{M}:s1"], notes="require.resolve is no change to any export.")
probe("closed-lib-twice-two-spellings", {"lib/a.js": A + "module.exports = { helper };\n", M: "const a = require('./lib/a');\nconst b = require('./lib/a.js');\n" + s(1, "a.helper()") + s(2, "b.helper()")},
      edge=[f"{M}:s1", f"{M}:s2"], notes="two spellings of one file are one exports object.")
probe("closed-props-and-late-property", {"lib/a.js": A + "function late() { return 'lib/a:late'; }\nmodule.exports = { helper };\nmodule.exports.late = late;\n",
      M: MAIN_A + s(1, "a.helper()") + s(2, "a.late()")},
      edge=[f"{M}:s1", f"{M}:s2"], notes="module.exports = {...} then module.exports.late = late: a property of the new object.")
# ---- packages and builtins: outside the repo ------------------------------------------------------------------------
probe("package-require", {"node_modules/pkg/index.js": "exports.f = function f() { return 'node_modules/pkg:f'; };\n",
      M: "const pkg = require('pkg');\nconst { f } = require('pkg');\nconst path = require('path');\n" + s(1, "pkg.f()") + s(2, "f()") + s(3, "path.join('a', 'b')")},
      notes="a package and a node builtin: outside the repo, so never an edge (and never a call that reaches a repo function).")
probe("package-reexport", {"node_modules/pkg/index.js": "exports.f = function f() { return 'node_modules/pkg:f'; };\n", "lib/b.js": "module.exports = require('pkg');\n",
      M: "const b = require('./lib/b');\n" + s(1, "b.f()")}, notes="module.exports = require('pkg').")
# ---- shapes that open or change the exports: a naive reading draws a wrong edge ----------------------------------------
probe("open-object-assign", {"lib/a.js": A + "exports.helper = helper;\nObject.assign(module.exports, { helper: function () { return 'lib/a:assigned'; } });\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="Object.assign over the exports: helper is the assigned function.")
probe("open-define-property", {"lib/a.js": A + "exports.helper = helper;\nObject.defineProperty(exports, 'helper', { value: function () { return 'lib/a:defined'; } });\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="exports passed to defineProperty (the property was writable, so it is replaced).")
probe("open-spread-in-literal", {"lib/more.js": "module.exports = { helper: function () { return 'lib/more:helper'; } };\n",
      "lib/a.js": A + "const more = require('./more');\nmodule.exports = { helper, ...more };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="a spread after helper replaces it.")
probe("open-computed-key", {"lib/a.js": A + "const key = 'hel' + 'per';\nmodule.exports = { helper, [key]: function () { return 'lib/a:computed'; } };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="a computed key that spells helper replaces it.")
probe("open-exports-passed-to-call", {"lib/a.js": A + "function install(target) { target.helper = function () { return 'lib/a:installed'; }; }\nexports.helper = helper;\ninstall(exports);\n",
      M: MAIN_A + s(1, "a.helper()")}, refuse={f"{M}:s1": "open"}, notes="exports handed to a function that writes it.")
probe("open-module-exports-passed", {"lib/a.js": A + "function install(target) { target.helper = function () { return 'lib/a:installed'; }; }\nmodule.exports = { helper };\ninstall(module.exports);\n",
      M: MAIN_A + s(1, "a.helper()")}, refuse={f"{M}:s1": "open"}, notes="module.exports handed to a function that writes it.")
probe("open-assigned-twice", {"lib/a.js": A + "module.exports = { helper };\nmodule.exports = { helper: function () { return 'lib/a:second'; } };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "more than once"}, notes="the second assignment wins.")
probe("open-property-written-twice", {"lib/a.js": A + "exports.helper = helper;\nexports.helper = function () { return 'lib/a:second'; };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "more than once"}, notes="the second write wins.")
probe("open-dead-alias-write-after-whole", {"lib/a.js": A + "module.exports = { helper };\nexports.helper = function () { return 'lib/a:dead'; };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "old object"}, notes="exports stopped being module.exports at the assignment: the later write goes to the old object, and helper stays.")
probe("open-dead-alias-write-before-whole", {"lib/a.js": A + "exports.helper = function () { return 'lib/a:dead'; };\nmodule.exports = { helper };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "old object"}, notes="the write before the assignment is lost with the old object.")
probe("open-conditional-write", {"lib/a.js": A + "exports.helper = helper;\nif (true) {\n  exports.helper = function () { return 'lib/a:conditional'; };\n}\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="a write in a branch.")
probe("open-conditional-whole", {"lib/a.js": A + "if (typeof module !== 'undefined' && module.exports) {\n  module.exports = { helper: function () { return 'lib/a:umd'; } };\n}\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="the UMD shape: the assignment is in an if.")
probe("open-write-in-function", {"lib/a.js": A + "exports.helper = helper;\nfunction init() { exports.helper = function () { return 'lib/a:init'; }; }\ninit();\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="a function writes exports and runs at load.")
probe("open-top-level-this", {"lib/a.js": A + "exports.helper = helper;\nthis.helper = function () { return 'lib/a:this'; };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "this"}, notes="`this` at the top of a CommonJS module is module.exports.")
probe("open-top-level-arrow-this", {"lib/a.js": A + "exports.helper = helper;\nconst swap = () => { this.helper = function () { return 'lib/a:arrow'; }; };\nswap();\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "this"}, notes="an arrow function's `this` is the module's.")
probe("open-this-in-exported-function", {"lib/a.js": A + "function boot() { this.helper = function () { return 'lib/a:booted'; }; }\nmodule.exports = { helper, boot };\n",
      M: MAIN_A + "a.boot();\n" + s(1, "a.helper()")}, refuse={f"{M}:s1": "this"}, notes="an exported function called as a.boot() has `this` = the exports.")
probe("open-arguments-top-level", {"lib/a.js": A + "exports.helper = helper;\narguments[0].helper = function () { return 'lib/a:arguments'; };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "arguments"}, notes="arguments[0] of the module wrapper is exports.")
probe("open-arguments-module", {"lib/a.js": A + "exports.helper = helper;\narguments[2].exports.helper = function () { return 'lib/a:module'; };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "arguments"}, notes="arguments[2] is the module.")
probe("open-eval", {"lib/a.js": A + "exports.helper = helper;\neval(\"exports.helper = function () { return 'lib/a:eval'; }\");\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "eval"}, notes="eval sees exports.")
probe("open-computed-write", {"lib/a.js": A + "exports.helper = helper;\nconst name = 'helper';\nexports[name] = function () { return 'lib/a:computed'; };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="exports[name] = ...")
probe("open-copy-loop", {"lib/more.js": "module.exports = { helper: function () { return 'lib/more:helper'; } };\n",
      "lib/a.js": A + "exports.helper = helper;\nconst more = require('./more');\nObject.keys(more).forEach(function (k) { exports[k] = more[k]; });\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="export * by hand: exports[k] = more[k].")
probe("open-module-exports-property-of-shadow", {"lib/a.js": A + "exports.helper = helper;\nfunction f() { var exports = {}; exports.helper = 1; return exports; }\n", M: MAIN_A + s(1, "a.helper()")},
      notes="a local named exports in a function: shadowing makes the file refuse (the write is not the module's, but ripple does not tell).")
probe("open-local-exports-variable", {"lib/a.js": A + "var exports = { helper: function () { return 'lib/a:local'; } };\nmodule.exports = { helper };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "declared"}, notes="`var exports` redeclares the wrapper's parameter.")
probe("open-local-module-parameter", {"lib/a.js": A + "function wrap(module) { module.exports = { helper: function () { return 'lib/a:wrapped'; } }; }\nmodule.exports = { helper };\nwrap({});\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "declared"}, notes="a parameter named module.")
probe("open-getter-member", {"lib/a.js": A + "module.exports = { get helper() { return function () { return 'lib/a:getter'; }; } };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "getter"}, notes="a getter returns the function that runs.")
probe("open-duplicate-key", {"lib/a.js": A + "module.exports = { helper, helper: function () { return 'lib/a:dup'; } };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "twice"}, notes="the second key wins.")
probe("open-proto-key", {"lib/a.js": "module.exports = { __proto__: { helper: function () { return 'lib/a:proto'; } } };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="helper comes from the prototype.")
probe("open-class-static-overwritten", {"lib/c.js": "class C { static make() { return 'lib/c:C.make'; } }\nmodule.exports = C;\nmodule.exports.make = function () { return 'lib/c:replaced'; };\n",
      M: "const C = require('./lib/c');\n" + s(1, "C.make()")}, refuse={f"{M}:s1": "class"}, notes="a property write replaces a static member of the exported class.")
probe("open-class-static-assigned-by-name", {"lib/c.js": "class C { static make() { return 'lib/c:C.make'; } }\nfunction other() { return 'lib/c:other'; }\nC.make = other;\nmodule.exports = C;\n",
      M: "const C = require('./lib/c');\n" + s(1, "C.make()")}, refuse={f"{M}:s1": "class"}, notes="C.make = other replaces the static member before the class is exported.")
probe("open-class-prototype-assigned", {"lib/c.js": "class C { run() { return 'lib/c:C.run'; } }\nfunction other() { return 'lib/c:other'; }\nC.prototype.run = other;\nmodule.exports = { C };\n",
      M: "const { C } = require('./lib/c');\n" + s(1, "new C().run()")}, refuse={f"{M}:s1": "class"}, notes="a method replaced on the prototype of an exported class.")
probe("open-function-export-reassigned-later", {"lib/f.js": "function f() { return 'lib/f:f'; }\nfunction g() { return 'lib/f:g'; }\nmodule.exports = f;\nmodule.exports = g;\n", M: "const f = require('./lib/f');\n" + s(1, "f()")},
      refuse={f"{M}:s1": "more than once"}, notes="two whole assignments: g wins.")
probe("open-instance-export", {"lib/a.js": "class A { run() { return 'lib/a:A.run'; } }\nmodule.exports = new A();\n", M: MAIN_A + s(1, "a.run()")},
      refuse={f"{M}:s1": "not a function or class"}, notes="the export is an instance: its methods are not followed.")
probe("open-function-expression-export", {"lib/a.js": "exports.helper = function () { return 'lib/a:helper'; };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "anonymous"}, notes="an anonymous function value has no symbol to name.")
probe("open-reassigned-local", {"lib/a.js": "let helper = function () { return 'lib/a:helper'; };\nfunction other() { return 'lib/a:other'; }\nmodule.exports = { helper };\nhelper = other;\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "bound"}, notes="the object captured the old value, and the name is bound twice.")
probe("open-not-a-function-member", {"lib/a.js": A + "module.exports = { helper, value: 3 };\n", M: MAIN_A + s(1, "a.value()")},
      notes="value is a number: the call throws, so no edge.")
probe("open-undefined-member", {"lib/a.js": A + "module.exports = { helper };\n", M: MAIN_A + s(1, "a.nothere()")},
      refuse={f"{M}:s1": "not exported"}, notes="a member the closed exports do not define.")
# ---- other files that change the exports ------------------------------------------------------------------------------
probe("foreign-write-through-binding", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "const a = require('./a');\na.helper = function () { return 'lib/patch:helper'; };\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="another file assigns a member through its own require of a.")
probe("foreign-write-through-inline-require", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "require('./a').helper = function () { return 'lib/patch:helper'; };\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="require('./a').helper = ...")
probe("foreign-write-computed", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "const a = require('./a');\nconst key = 'help' + 'er';\na[key] = function () { return 'lib/patch:computed'; };\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="a[key] = ... through the binding.")
probe("foreign-object-assign", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "Object.assign(require('./a'), { helper: function () { return 'lib/patch:assigned'; } });\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="the exports passed to Object.assign from another file.")
probe("foreign-passed-on", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "const a = require('./a');\nfunction patch(target) { target.helper = function () { return 'lib/patch:passed'; }; }\npatch(a);\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="the exports binding handed to a function.")
probe("foreign-aliased", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "const a = require('./a');\nconst same = a;\nsame.helper = function () { return 'lib/patch:alias'; };\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="const same = a; same.helper = ...")
probe("foreign-write-through-reexport", {"lib/a.js": A + "module.exports = { helper };\n", "lib/b.js": "module.exports = require('./a');\n",
      "lib/patch.js": "const b = require('./b');\nb.helper = function () { return 'lib/patch:reexport'; };\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="b is a's exports object: a write through b changes a.")
probe("foreign-nested-write", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "const a = require('./a');\na.helper.cache = {};\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="a write below a member (a.helper.cache = ...) changes the function, not the exports (node still reaches helper), but the member may hold another exports object: refused.")
probe("foreign-delete", {"lib/a.js": A + "module.exports = { helper };\n", "lib/patch.js": "const a = require('./a');\ndelete a.helper;\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="delete a.helper.")
probe("foreign-write-from-esm-default", {"lib/a.cjs": A + "module.exports = { helper };\n", "patch.mjs": "import a from './lib/a.cjs';\na.helper = function () { return 'patch:esm'; };\n",
      "main.cjs": "const a = require('./lib/a.cjs');\n" + s(1, "a.helper()")}, entries=("patch.mjs", "main.cjs"),
      refuse={"main.cjs:s1": "open"}, notes="an ES default import is module.exports: a write through it changes the CommonJS file.")
probe("foreign-write-from-dynamic-import", {"lib/a.cjs": A + "module.exports = { helper };\n", "patch.mjs": "const ns = await import('./lib/a.cjs');\nns.default.helper = function () { return 'patch:dynamic'; };\n",
      "main.cjs": "const a = require('./lib/a.cjs');\n" + s(1, "a.helper()")}, entries=("patch.mjs", "main.cjs"),
      refuse={"main.cjs:s1": "open"}, notes="await import() hands out the same module.exports.")
probe("foreign-require-cache", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "require.cache[require.resolve('./a')].exports.helper = function () { return 'lib/patch:cache'; };\n", M: MAIN_A + "require('./lib/patch');\n" + s(1, "a.helper()")},
      refuse={f"{M}:s1": "require.cache"}, notes="the module cache holds every exports object.")
probe("foreign-dynamic-require-write", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "const name = './a';\nconst a = require(name);\na.helper = function () { return 'lib/patch:dynamic'; };\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="a computed require whose result is written may be any module.")
probe("foreign-createrequire", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "const { createRequire } = require('module');\nconst r = createRequire(__filename);\nr('./a').helper = function () { return 'lib/patch:createRequire'; };\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "module"}, notes="createRequire gives another require over the same cache.")
probe("foreign-require-as-value", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "function load(req) { req('./a').helper = function () { return 'lib/patch:value'; }; }\nload(require);\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "require"}, notes="require handed to a function.")
probe("open-define-getter-method", {"lib/a.js": A + "exports.helper = helper;\nexports.__defineGetter__('helper', function () { return function () { return 'lib/a:getter'; }; });\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "open"}, notes="__defineGetter__ is a method of every object, and it replaces the property.")
probe("foreign-define-getter-method", {"lib/a.js": A + "module.exports = { helper };\n",
      "lib/patch.js": "const a = require('./a');\na.__defineGetter__('helper', function () { return function () { return 'lib/patch:getter'; }; });\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "another file"}, notes="the same through a require of the file.")
probe("with-statement-shadows-the-binding", {"lib/a.js": A + "module.exports = { helper };\n",
      M: MAIN_A + "with ({ a: { helper: function () { return 'main:with'; } } }) {\n  exports.s1 = function () { return a.helper(); }; // site s1\n}\n"},
      refuse={f"{M}:s1": "with"}, notes="inside `with`, a is the object's: a statement that makes every name in the file unreliable.")
# ---- circular requires --------------------------------------------------------------------------------------------------
probe("cycle-whole-assignment", {"lib/a.js": "const b = require('./b');\nfunction x() { return 'lib/a:x'; }\nmodule.exports = { x };\n",
      "lib/b.js": "const a = require('./a');\n" + s(1, "a.x()")}, entries=("lib/a.js", "lib/b.js"),
      refuse={"lib/b.js:s1": "cycle"}, notes="b holds a's first exports object, which module.exports = replaces: a.x is undefined for b, for ever.")
probe("cycle-property-writes", {"lib/a.js": "const b = require('./b');\nfunction x() { return 'lib/a:x'; }\nexports.x = x;\n",
      "lib/b.js": "const a = require('./a');\n" + s(1, "a.x()")}, entries=("lib/a.js", "lib/b.js"),
      refuse={"lib/b.js:s1": "cycle"}, notes="exports.x = x keeps the object, so a later call from b reaches a:x; ripple refuses a require that is in a cycle at all.")
# ---- the binding that reaches the module ---------------------------------------------------------------------------------
probe("shadowed-require-parameter", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "const a = require('./lib/a');\nfunction run(require) {\n  const b = require('./lib/a');\n  return b.helper(); // site s1\n}\nexports.s1 = function () { return run(function () { return { helper: function () { return 'main:fake'; } }; }); };\nexports.s2 = function () { return a.helper(); }; // site s2\n"},
      refuse={f"{M}:s1": "require"}, edge=[], notes="a parameter named require is not node's: s1 reaches the fake, s2 (the real one) is outside the function.")
probe("shadowed-require-const", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "var require = () => ({ helper: () => 'main:fake' });\nconst a = require('./lib/a');\n" + s(1, "a.helper()")},
      refuse={f"{M}:s1": "require"}, notes="var require = ... at the top (a const would be a SyntaxError: require is a parameter of the wrapper).")
probe("shadowed-require-assigned", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "require = () => ({ helper: () => 'main:fake' });\nconst a = require('./lib/a');\n" + s(1, "a.helper()")},
      refuse={f"{M}:s1": "require"}, notes="require assigned a new function.")
probe("shadowed-require-function", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "function require() { return { helper: () => 'main:fake' }; }\nconst a = require('./lib/a');\n" + s(1, "a.helper()")},
      refuse={f"{M}:s1": "require"}, notes="a function declaration named require replaces the parameter.")
probe("dynamic-require-concat", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "const name = 'a';\nconst a = require('./lib/' + name);\n" + s(1, "a.helper()")}, refuse={f"{M}:s1": "computed"}, notes="require(string + string).")
probe("dynamic-require-template", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "const name = 'a';\nconst a = require(`./lib/${name}`);\n" + s(1, "a.helper()")}, refuse={f"{M}:s1": "computed"}, notes="require of a template with a substitution.")
probe("dynamic-require-variable", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "const where = './lib/a';\nconst a = require(where);\n" + s(1, "a.helper()")}, refuse={f"{M}:s1": "computed"}, notes="require(variable).")
probe("conditional-require-ternary", {"lib/a.js": A + "module.exports = { helper };\n", "lib/b.js": "function helper() { return 'lib/b:helper'; }\nmodule.exports = { helper };\n",
      M: "const a = process.argv.length > 99 ? require('./lib/a') : require('./lib/b');\n" + s(1, "a.helper()")}, refuse={f"{M}:s1": "assigned"}, notes="which file is required depends on a condition.")
probe("conditional-require-assigned", {"lib/a.js": A + "module.exports = { helper };\n", "lib/b.js": "function helper() { return 'lib/b:helper'; }\nmodule.exports = { helper };\n",
      M: "let a;\nif (process.argv.length > 99) {\n  a = require('./lib/a');\n} else {\n  a = require('./lib/b');\n}\n" + s(1, "a.helper()")}, refuse={f"{M}:s1": "assigned"}, notes="two assignments of one name.")
probe("reassigned-require-binding", {"lib/a.js": A + "module.exports = { helper };\n", "lib/b.js": "function helper() { return 'lib/b:helper'; }\nmodule.exports = { helper };\n",
      M: "let a = require('./lib/a');\na = require('./lib/b');\n" + s(1, "a.helper()")}, refuse={f"{M}:s1": "more than once"}, notes="the binding is reassigned: b wins.")
probe("redeclared-require-binding", {"lib/a.js": A + "module.exports = { helper };\n", "lib/b.js": "function helper() { return 'lib/b:helper'; }\nmodule.exports = { helper };\n",
      M: "var a = require('./lib/a');\nvar a = require('./lib/b');\n" + s(1, "a.helper()")}, refuse={f"{M}:s1": "more than once"}, notes="var a twice: b wins.")
probe("destructure-then-reassign", {"lib/a.js": A + "module.exports = { helper };\n",
      M: "let { helper } = require('./lib/a');\nhelper = function () { return 'main:replaced'; };\n" + s(1, "helper()")}, refuse={f"{M}:s1": "more than once"}, notes="the destructured name is assigned again.")
probe("require-missing-file", {M: "exports.s1 = function () {\n  try {\n    const a = require('./lib/nope');\n    return a.helper(); // site s1\n  } catch (e) {\n    throw e;\n  }\n};\n"},
      refuse={f"{M}:s1": "no file"}, notes="no such file: node throws, and ripple says nothing is named.")
probe("require-json", {"lib/data.json": "{\"helper\": 3}\n", M: "const data = require('./lib/data.json');\n" + s(1, "data.helper()")}, refuse={f"{M}:s1": "JSON"}, notes="a JSON file is data.")
probe("require-json-and-js", {"lib/a.js": A + "module.exports = { helper };\n", "lib/a.json": "{\"helper\": 3}\n", M: MAIN_A + s(1, "a.helper()")},
      edge=[f"{M}:s1"], notes="./lib/a is a.js: Node tries .js before .json.")
probe("require-json-only-beats-index", {"lib/a.json": "{\"helper\": 3}\n", "lib/a/index.js": "function helper() { return 'lib/a:helper'; }\nmodule.exports = { helper };\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "JSON"}, notes="./lib/a is a.json (Node tries .json before the directory): the call throws.")
probe("require-jsx-not-tried", {"lib/a.jsx": "function helper() { return 'lib/a:helper'; }\nmodule.exports = { helper };\n", M: "exports.s1 = function () {\n  const a = require('./lib/a');\n  return a.helper(); // site s1\n};\n"},
      refuse={f"{M}:s1": "no file"}, notes="Node's require does not try .jsx: a bundler might.")
probe("require-directory-package-main", {"lib/package.json": "{\"main\": \"entry.js\"}\n", "lib/entry.js": "function helper() { return 'lib/entry:helper'; }\nmodule.exports = { helper };\n", "lib/index.js": "function helper() { return 'lib:helper'; }\nmodule.exports = { helper };\n",
      M: "const lib = require('./lib');\n" + s(1, "lib.helper()")}, refuse={f"{M}:s1": "package.json"}, notes="a directory with a package.json: main picks the file, which ripple does not read.")
probe("esm-package-require-esm", {"package.json": "{\"type\": \"module\"}\n", "lib/a.js": "export function helper() { return 'lib/a:helper'; }\n", "main.cjs": "const a = require('./lib/a.js');\n" + s(1, "a.helper()")},
      entries=("main.cjs",), refuse={"main.cjs:s1": "ES module"}, notes="require() of an ES module: node 24 loads it; ripple does not follow it.")
probe("esm-syntax-without-type", {"lib/a.js": "export function helper() { return 'lib/a:helper'; }\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "ES module"}, notes="no package.json type: node 24 reads the syntax and runs it as ES.")
probe("esm-imports-cjs-default", {"lib/a.cjs": A + "module.exports = { helper };\n", "main.mjs": "import a from './lib/a.cjs';\n" + esm_s(1, "a.helper()")}, entries=("main.mjs",),
      refuse={"main.mjs:s1": "CommonJS"}, notes="import a from a CommonJS file: a is module.exports. Not followed.")
probe("esm-imports-cjs-named", {"lib/a.cjs": A + "exports.helper = helper;\n", "main.mjs": "import { helper } from './lib/a.cjs';\n" + esm_s(1, "helper()")}, entries=("main.mjs",),
      refuse={"main.mjs:s1": "CommonJS"}, notes="a named import of a CommonJS file: found by node's scan of the file. Not followed.")
probe("esm-imports-cjs-namespace", {"lib/a.cjs": A + "exports.helper = helper;\n", "main.mjs": "import * as a from './lib/a.cjs';\n" + esm_s(1, "a.helper()")}, entries=("main.mjs",),
      refuse={"main.mjs:s1": "CommonJS"}, notes="import * as a from a CommonJS file.")
probe("cjs-extension-in-module-package", {"package.json": "{\"type\": \"module\"}\n", "lib/a.cjs": A + "module.exports = { helper };\n", "main.cjs": "const a = require('./lib/a.cjs');\n" + s(1, "a.helper()")},
      entries=("main.cjs",), edge=["main.cjs:s1"], notes=".cjs is CommonJS whatever the package type says.")
probe("type-commonjs-explicit", {"package.json": "{\"type\": \"commonjs\"}\n", "lib/a.js": A + "module.exports = { helper };\n", M: MAIN_A + s(1, "a.helper()")},
      edge=[f"{M}:s1"], notes="type commonjs: .js is CommonJS.")
probe("nested-package-type-module", {"lib/package.json": "{\"type\": \"module\"}\n", "lib/a.js": "export function helper() { return 'lib/a:helper'; }\n", M: MAIN_A + s(1, "a.helper()")},
      refuse={f"{M}:s1": "ES module"}, notes="the nearest package.json type decides: lib/ is a module package.")
probe("own-exports-call", {"lib/a.js": "function helper() { return 'lib/a:helper'; }\nexports.helper = helper;\nexports.s1 = function () { return exports.helper(); }; // site s1\n", M: "require('./lib/a');\n"},
      entries=("lib/a.js", M), notes="a call through the file's own exports object: not a call to code outside the repo, whatever it reaches.")
probe("module-parent-test", {"lib/a.js": A + "module.exports = { helper };\n", M: MAIN_A + "if (!module.parent) {\n  console.log('main');\n}\n" + s(1, "a.helper()")},
      edge=[f"{M}:s1"], notes="!module.parent is a test, not a use of the module object.")

# ---- the review of the first CommonJS model (round 1): each case c1 to c22 as node ran it, then the members of its class ------------
# main.js requires lib/patch.js, which changes (or may change) what lib/a's `helper` is, then calls it; node says what it reached.
R = "function () { return 'patched'; }"
LIB_A = A + "module.exports = { helper };\n"
CALL_A = "a.helper()"


def P(text):
    return text.replace("%R%", R)


def patched(name, patch, part="another file", notes="", extra=None, main=None, entries=("main.js",), edge=(), links=None, patch_file="lib/patch.js", refuse=True):
    """lib/a.js exports helper; main requires `patch_file`, which holds `patch`, then calls a.helper() as site s1."""
    files = {"lib/a.js": LIB_A, M: main or (f"require('./{patch_file[:-3] if patch_file.endswith('.js') else patch_file}');\n" + MAIN_A + s(1, CALL_A))}
    if patch is not None:
        files[patch_file] = P(patch)
    files.update({k: P(v) for k, v in (extra or {}).items()})
    probe(name, files, entries=entries, edge=edge, refuse={f"{M}:s1": part} if refuse else {}, notes=notes, links=links)


# A: every use of a name bound from a require is a form proven harmless, or it opens the target
patched("r1-c19-shorthand-property", "const a = require('./a');\nconst o = { a };\no.a.helper = %R%;\n", notes="c19: { a } holds the exports object, which a write through o reaches.")
patched("r1-c21-barrel", "require('./index').a.helper = %R%;\n", extra={"lib/index.js": "const a = require('./a');\nmodule.exports = { a };\n"},
        notes="c21: a barrel keeps a's exports in a member, and a write through the barrel changes a.")
patched("a-array-element", "const a = require('./a');\nconst list = [a];\nlist[0].helper = %R%;\n")
patched("a-returned", "const a = require('./a');\nfunction get() { return a; }\nget().helper = %R%;\n")
patched("a-arrow-body", "const a = require('./a');\nconst get = () => a;\nget().helper = %R%;\n")
patched("a-default-parameter", "const a = require('./a');\nfunction f(x = a) { x.helper = %R%; }\nf();\n")
patched("a-destructuring-default", "const a = require('./a');\nconst { z = a } = {};\nz.helper = %R%;\n")
patched("a-ternary", "const a = require('./a');\nconst x = process.argv.length > 0 ? a : {};\nx.helper = %R%;\n")
patched("a-logical-or", "const a = require('./a');\nconst x = a || {};\nx.helper = %R%;\n")
patched("a-assigned-to-a-variable", "const a = require('./a');\nlet x;\nx = a;\nx.helper = %R%;\n")
patched("a-sequence-expression", "const a = require('./a');\n(0, a).helper = %R%;\n")
patched("a-awaited", "const a = require('./a');\n(async function () { (await a).helper = %R%; })();\n", refuse=False,
        notes="the write runs after the call (a microtask): node reaches helper, and the await still opens the exports.")
patched("a-class-field", "const a = require('./a');\nclass K { x = a; }\nnew K().x.helper = %R%;\n")
patched("a-static-field", "const a = require('./a');\nclass K { static x = a; }\nK.x.helper = %R%;\n")
patched("a-for-of", "const a = require('./a');\nfor (const x of [a]) { x.helper = %R%; }\n")
patched("a-spread-argument", "const a = require('./a');\nfunction f(x) { x.helper = %R%; }\nf(...[a]);\n")
patched("a-object-method-returns", "const a = require('./a');\nconst o = { get() { return a; } };\no.get().helper = %R%;\n")
patched("a-unicode-escape-spelling", "const a = require('./a');\n\\u0061.helper = %R%;\n", notes="\\u0061 is the name a: no text match finds it.", part="escape")
patched("a-exported-by-property", "const a = require('./a');\nexports.a = a;\n", main="require('./lib/patch').a.helper = " + R + ";\n" + MAIN_A + s(1, CALL_A))
patched("a-whole-export-is-the-require", "const a = require('./a');\nmodule.exports = a;\n", main="require('./lib/patch').helper = " + R + ";\n" + MAIN_A + s(1, CALL_A))
patched("a-inline-require-in-a-property", "exports.a = require('./a');\n", main="require('./lib/patch').a.helper = " + R + ";\n" + MAIN_A + s(1, CALL_A))
patched("a-reexport-chain", "require('./c').helper = %R%;\n", extra={"lib/b.js": "module.exports = require('./a');\n", "lib/c.js": "module.exports = require('./b');\n"},
        notes="c is b is a: a write through the end of a chain of re-exports (kills the group union of re-exports).")
patched("a-derived-alias-chain", "const a = require('./a');\nconst b = a;\nconst c = b;\nc.helper = %R%;\n")
patched("a-valueof-hands-the-exports-back", "const a = require('./a');\na.valueOf().helper = %R%;\n", notes="Object.prototype.valueOf returns its receiver: the exports.")
patched("a-computed-method-name", "const a = require('./a');\nconst k = '__defineGetter__';\na[k]('helper', function () { return " + R + "; });\n", notes="a method called by a name computed at run time may be __defineGetter__.")
patched("b-package-main-in-a-hidden-directory", "require('../../a').helper = %R%;\n", patch_file="lib/patchdir/.hidden/index.js",
        main="require('./lib/patchdir');\n" + MAIN_A + s(1, CALL_A), extra={"lib/patchdir/package.json": "{\"main\": \".hidden/index.js\"}\n"}, part="does not read",
        notes="a directory whose package.json main is a file in a hidden directory: node runs it, ripple does not read it.")
patched("a-nested-write-below-a-member", "const a = require('./a');\na.helper.cache = {};\n", refuse=False, edge=[],
        notes="a write below a member is refused as a write through the binding (the member may hold another exports object): no edge, not wrong.")
probe("a-harmless-uses-keep-the-edge", {"lib/a.js": LIB_A, "lib/patch.js": "const a = require('./a');\nif (typeof a !== 'object') throw new Error('x');\nif (a === undefined || a === null) throw new Error('y');\nif (!a) throw new Error('z');\nif (a) { a.helper.call(null); }\nconst h = a.helper;\nh();\nconst { helper } = a;\nhelper();\nconst k = h;\nk();\nvoid a;\n",
      M: "require('./lib/patch');\n" + MAIN_A + s(1, CALL_A)}, edge=[f"{M}:s1"],
      notes="calling a member or the value, typeof, comparing, testing, and a member read into a name that is only called: none of these writes.")
probe("a-names-that-are-not-references", {"lib/a.js": LIB_A, M: "const a = require('./lib/a');\nconst o = { a: 1, 'a': 2 };\nconst t = o.a + '|a';\nexports.t = t;\nlbl: for (;;) { break lbl; }\nclass K { a() { return 1; } }\n" + s(1, CALL_A)},
      edge=[f"{M}:s1"], notes="a key, a property, a string and a method named like the binding are no use of it.")

# B: which files run
patched("r1-c01-new-target", "function F() { if (!new.target) return new F(); }\nrequire('./a').helper = %R%;\n", notes="c1: new.target is no ES module syntax.")
patched("r1-c01b-without-new-target", "function F() { return 1; }\nrequire('./a').helper = %R%;\n", notes="c1b: the same file without new.target.")
patched("r1-c02-jsx-file", "require('./a').helper = %R%;\n", patch_file="lib/patch.jsx", main="require('./lib/patch.jsx');\n" + MAIN_A + s(1, CALL_A), notes="c2: node runs any extension but .json and .node as CommonJS.")
patched("r1-c03-type-wrong-case", "require('../lib/a').helper = %R%;\n", patch_file="p/patch.js", main="require('./p/patch');\n" + MAIN_A + s(1, CALL_A),
        extra={"p/package.json": "{\"type\":\"CommonJS\"}\n"}, notes="c3: a string type that is not module or commonjs is no type to node 24.")
patched("b-type-empty-string", "require('../lib/a').helper = %R%;\n", patch_file="p/patch.js", main="require('./p/patch');\n" + MAIN_A + s(1, CALL_A),
        extra={"p/package.json": "{\"type\":\"\"}\n"}, notes="an empty type is none.")
patched("r1-c04-txt-file", "require('./a').helper = %R%;\n", patch_file="lib/patch.txt", main="require('./lib/patch.txt');\n" + MAIN_A + s(1, CALL_A), notes="c4: any extension runs as CommonJS.", part="does not read")
patched("b-extensionless-file", "require('./a').helper = %R%;\n", patch_file="lib/patchfile", main="require('./lib/patchfile');\n" + MAIN_A + s(1, CALL_A), notes="a file with no extension is tried first, as it is written.", part="does not read")
patched("r1-c06-hidden-directory", "require('../lib/a').helper = %R%;\n", patch_file=".hidden/patch.js", main="require('./.hidden/patch');\n" + MAIN_A + s(1, CALL_A), notes="c6: the walk skips hidden directories.", part="does not read")
patched("b-skipped-build-directory", "require('../lib/a').helper = %R%;\n", patch_file="dist/patch.js", main="require('./dist/patch');\n" + MAIN_A + s(1, CALL_A), notes="the walk skips dist at the root.", part="does not read")
patched("r1-c08-committed-package", "require('../../lib/a').helper = %R%;\n", patch_file="node_modules/pkg/index.js", main="require('pkg');\n" + MAIN_A + s(1, CALL_A), notes="c8: a package committed into the repo is repo code.", part="committed into the repo")
patched("b-committed-scoped-package", "require('../../../lib/a').helper = %R%;\n", patch_file="node_modules/@s/p/index.js", main="require('@s/p');\n" + MAIN_A + s(1, CALL_A), part="committed into the repo")
patched("b-await-as-a-name", "var await = 1;\nrequire('./a').helper = %R%;\n", notes="await is a name in a CommonJS file: it is no top-level await.")
patched("b-parse-error-file", "var x = 1; var y = x?.5:1;\nrequire('./a').helper = %R%;\n", notes="valid JavaScript the tree-sitter grammar does not read (a file that did not parse that says require).", part="did not parse")

# C: the module wrapper's names
patched("r1-c15-require-parameter", "function load(require) { return require('fs'); }\nrequire('./a').helper = %R%;\n", notes="c15: a parameter named require in another function.", part="may name any file")
patched("c-arrow-parameter", "const load = (require) => require('fs');\nrequire('./a').helper = %R%;\n", part="may name any file")
patched("c-catch-parameter", "try { throw 1; } catch (require) { }\nrequire('./a').helper = %R%;\n", part="may name any file")
patched("c-destructured-parameter", "function load({ require }) { return require('fs'); }\nrequire('./a').helper = %R%;\n", part="may name any file")
patched("c-nested-var", "function load() { var require = function (x) { return x; }; return require('fs'); }\nrequire('./a').helper = %R%;\n", part="may name any file")
patched("c-block-let", "{ let require = 1; }\nrequire('./a').helper = %R%;\n", part="may name any file")
patched("c-umd-factory", "(function (root, factory) { factory(root); })(this, function (require) { return 1; });\nrequire('./a').helper = %R%;\n", part="may name any file")
patched("c-method-parameter", "class K { load(require) { return require('fs'); } }\nrequire('./a').helper = %R%;\n", part="may name any file")

# D: ways into the module system
patched("r1-c05-getbuiltinmodule", "process.getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n", part="process", notes="c5.")
patched("r1-c20-process-alias", "const p = process;\np.getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n", part="process", notes="c20: process under another name.")
patched("d-process-mainmodule", "const p = process;\nconst load = p.mainModule ? p.mainModule.require.bind(p.mainModule) : p.getBuiltinModule('module').createRequire(__filename);\nload('./a').helper = %R%;\n", part="process")
patched("d-global-process", "global.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n", part="global")
patched("d-globalthis-alias", "const g = globalThis;\ng.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n", part="globalThis")
patched("d-function-constructor", "const P = Function('return process')();\nP.getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n", part="Function")
patched("d-constructor-of-a-function", "const P = (function () {}).constructor('return process')();\nP.getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n", part="constructor")
patched("d-vm-run-in-this-context", "require('vm').runInThisContext(\"process.getBuiltinModule('module').createRequire(process.cwd() + '/x.js')('./lib/a').helper = function () { return 'patched'; }\");\n", part="vm")
patched("d-module-require", "module.require('./a').helper = %R%;\n", part="module.require")
patched("d-module-children", "require('./a');\nmodule.children[0].exports.helper = %R%;\n", part="module.children")
patched("d-module-constructor", "module.constructor._load('./a', module).helper = %R%;\n", part="constructor")
patched("d-dynamic-import-of-module", "import('node:module').then((m) => { m.createRequire(__filename)('./a').helper = %R%; });\n", part="module", refuse=True,
        notes="the write runs after the call, so node reaches helper; the import() of the module builtin still opens everything.")
probe("d-esm-imports-module", {"lib/a.cjs": LIB_A, "patch.mjs": "import { createRequire } from 'module';\ncreateRequire(import.meta.url)('./lib/a.cjs').helper = " + R + ";\n",
      "main.cjs": "require('./lib/a.cjs');\n" + "const a = require('./lib/a.cjs');\n" + s(1, CALL_A)}, entries=("patch.mjs", "main.cjs"), refuse={"main.cjs:s1": "module"},
      notes="an ES module imports the module builtin (kills the import check).")
probe("d-harmless-reflective-names-keep-the-edge", {"lib/a.js": LIB_A, "lib/patch.js": "const env = process.env.NODE_ENV || 'x';\nconst args = process.argv.length;\nprocess.nextTick(function () {});\nif (typeof global !== 'undefined' && typeof globalThis !== 'undefined') { exports.ok = env + args; }\nif (typeof module !== 'undefined' && module.exports) { exports.m = module.id; }\nconst r = require.resolve('./a');\nexports.r = typeof r;\nfunction F() {} const isF = F instanceof Function;\nexports.f = isF;\n",
      M: "require('./lib/patch');\n" + MAIN_A + s(1, CALL_A)}, edge=[f"{M}:s1"],
      notes="process.env, process.argv, process.nextTick, typeof global, module.exports, require.resolve and instanceof Function reach no loader.")
probe("d-more-harmless-forms-keep-the-edge", {"lib/a.js": LIB_A, "lib/patch.js": "'use strict';\nconst p = process.platform + process.version;\nprocess.stdout.write('');\nprocess.on('exit', function () {});\nprocess.exitCode = 0;\nconst t = typeof process + typeof require + typeof module + typeof Function;\nconst c = Function.prototype.call;\nclass K {}\nconst name = new K().constructor.name;\nconst same = new K().constructor === K;\nconst pr = K.prototype;\nfunction E() { Error.captureStackTrace(this, this.constructor); }\nglobal.setTimeout(function () {}, 0);\nglobalThis.console.log === undefined;\nglobal.counter = 1;\nconst main = require.main === module;\nconst id = module.id + module.filename + module.loaded + module.path;\nconst top = !module.parent;\nconst here = module.parent === null;\nexports.z = [p, t, c, name, same, pr, E, main, id, top, here];\n",
      M: "require('./lib/patch');\n" + MAIN_A + s(1, CALL_A)}, edge=[f"{M}:s1"],
      notes="each of these reads a value and reaches no loader: the process's values and streams, typeof, Function.prototype, a constructor's name or identity, the global timers, require.main and module's plain fields.")
patched("d-process-subscript", "process['getBuiltinModule']('module').createRequire(__filename)('./a').helper = %R%;\n", part="process")
patched("d-function-as-a-value", "const F = Function;\nconst P = F('return process')();\nP.getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n", part="Function")
patched("d-process-binding-read", "const b = process.binding;\nprocess.getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n", part="process")
patched("d-computed-require-of-the-module-builtin", "const m = require(['mod', 'ule'].join(''));\nm.createRequire(__filename)('./a').helper = %R%;\n", part="computed")
probe("d-computed-relative-require-opens-everything", {"lib/a.js": LIB_A, "lib/other.js": "function other() { return 'lib/other:other'; }\nmodule.exports = { other };\n",
      M: MAIN_A + "const name = 'other';\nconst x = require('./lib/' + name);\n" + s(1, CALL_A) + s(2, "x.other()")}, refuse={f"{M}:s1": "computed", f"{M}:s2": "computed"},
      notes="a computed specifier that starts with ./ is no builtin, but it may name a file ripple does not read (a hidden one, a .txt, one in node_modules): it opens everything.")
patched("d-eval-indirect", "const run = eval;\nrun(\"process.getBuiltinModule('module').createRequire(process.cwd() + '/x.js')('./lib/a').helper = function () { return 'patched'; }\");\n", part="eval")

# E: an exported name means one function for the whole run
probe("r1-c14-annex-b-block-function", {"lib/a.js": "function helper() { return 'lib/a:A'; }\nif (true) { function helper() { return 'lib/a:B'; } }\nmodule.exports = { helper };\n", M: MAIN_A + s(1, CALL_A)},
      refuse={f"{M}:s1": "more than once"}, notes="c14: a function declared in a block is assigned to the same name when the block runs: node calls B.")
probe("r1-c14-duplicate-top-level", {"lib/a.js": "function helper() { return 'lib/a:A'; }\nmodule.exports = { helper };\nfunction helper() { return 'lib/a:B'; }\n", M: MAIN_A + s(1, CALL_A)},
      refuse={f"{M}:s1": "more than once"}, notes="c14: two declarations of one name are legal in a CommonJS file, and the second wins.")
probe("r1-c14-var-assigned-after-export", {"lib/a.js": "exports.helper = helper;\nvar helper = function () { return 'lib/a:helper'; };\n", M: MAIN_A + s(1, CALL_A)},
      refuse={f"{M}:s1": "after"}, notes="c14: exports.helper is undefined: the var holds no function when the statement runs.")
probe("r1-c14-var-after-export-and-consumer-default", {"lib/a.js": "exports.helper = helper;\nvar helper = function () { return 'lib/a:helper'; };\n",
      M: "const { helper = function () { return 'default'; } } = require('./lib/a');\n" + s(1, "helper()")}, refuse={f"{M}:s1": "destructured"}, notes="c14: the default is what runs.")
probe("e-consumer-default-with-the-member-present", {"lib/a.js": LIB_A, M: "const { helper = function () { return 'default'; } } = require('./lib/a');\n" + s(1, "helper()")},
      refuse={f"{M}:s1": "destructured"}, notes="a default on the consumer side: which function runs depends on the member being there.")
probe("e-switch-case-function", {"lib/a.js": "switch (1) { case 1: function helper() { return 'lib/a:B'; } }\nfunction helper() { return 'lib/a:A'; }\nmodule.exports = { helper };\n", M: MAIN_A + s(1, CALL_A)},
      refuse={f"{M}:s1": "more than once"}, notes="a function in a case clause is declared again when the case runs.")
probe("e-try-block-function", {"lib/a.js": "try { function helper() { return 'lib/a:B'; } } catch (e) { }\nfunction helper() { return 'lib/a:A'; }\nmodule.exports = { helper };\n", M: MAIN_A + s(1, CALL_A)},
      refuse={f"{M}:s1": "more than once"}, notes="a function in a try block.")
probe("e-whole-export-of-a-name-declared-twice", {"lib/a.js": "function helper() { return 'lib/a:A'; }\nif (true) { function helper() { return 'lib/a:B'; } }\nmodule.exports = helper;\n", M: "const helper = require('./lib/a');\n" + s(1, "helper()")},
      refuse={f"{M}:s1": "more than once"}, notes="module.exports = helper, where two declarations of helper are in the file.")
probe("e-export-of-a-require-bound-name-assigned-in-a-function", {"lib/a.js": LIB_A, "lib/b.js": "let { helper } = require('./a');\nfunction swap() { helper = function () { return 'lib/b:swapped'; }; }\nswap();\nexports.helper = helper;\n",
      M: "const b = require('./lib/b');\n" + s(1, "b.helper()")}, refuse={f"{M}:s1": "no function declared"}, notes="the name was assigned in a function before the export statement ran: b exports a name bound by a require, which may be any function.")
probe("e-function-in-a-top-level-block-alone", {"lib/a.js": "if (true) { var helper = function () { return 'lib/a:helper'; }; }\nmodule.exports = { helper };\n", M: MAIN_A + s(1, CALL_A)},
      refuse={f"{M}:s1": "no function declared"}, notes="a var in a block is the name for the whole file, but whether the block runs is not known.")
probe("h-node-names-the-directory-the-compiler-names-the-jsx", {"lib/a.jsx": "export function helper() { return 'lib/a.jsx:helper'; }\n", "lib/a/index.js": "function helper() { return 'lib/a/index:helper'; }\nmodule.exports = { helper };\n", M: MAIN_A + s(1, CALL_A)},
      refuse={f"{M}:s1": "Node"}, notes="require('./lib/a') is lib/a/index.js to node (no .jsx is tried) and lib/a.jsx to the compiler.")
probe("e-parameter-named-like-the-export", {"lib/a.js": "function helper() { return 'lib/a:helper'; }\nfunction g(helper) { return helper; }\nmodule.exports = { helper };\n", M: MAIN_A + s(1, CALL_A)},
      notes="another binding of the name, in a function: refused (a name is not told from its shadow), never wrong.")
probe("e-var-function-before-the-export", {"lib/a.js": "var helper = function () { return 'lib/a:helper'; };\nmodule.exports = { helper };\n", M: MAIN_A + s(1, CALL_A)},
      edge=[f"{M}:s1"], notes="a var holding a function, assigned before the export statement runs.")
probe("e-block-function-alone", {"lib/a.js": "if (true) { function helper() { return 'lib/a:helper'; } }\nmodule.exports = { helper };\n", M: MAIN_A + s(1, CALL_A)},
      notes="the only declaration is in a block: whether it runs is not known, so no edge (never wrong).")
probe("r1-c22-load-time-call-in-a-cycle", {"lib/a.js": "const b = require('./b');\nfunction x() { return 'lib/a:x'; }\nexports.x = x;\n",
      "lib/b.js": "const a = require('./a');\ntry { exports.s1 = { reached: a.x() }; } catch (e) { exports.s1 = { threw: e.name }; } // site s1\n", M: "require('./lib/a');\n"},
      entries=("main.js", "lib/b.js"), refuse={"lib/b.js:s1": "cycle"}, notes="c22: b runs while a is loading, and a.x is not set yet: node throws.")
probe("e-load-time-call-in-a-cycle-after-the-export", {"lib/a.js": "function x() { return 'lib/a:x'; }\nexports.x = x;\nconst b = require('./b');\n",
      "lib/b.js": "const a = require('./a');\ntry { exports.s1 = { reached: a.x() }; } catch (e) { exports.s1 = { threw: e.name }; } // site s1\n", M: "require('./lib/a');\n"},
      entries=("main.js", "lib/b.js"), refuse={"lib/b.js:s1": "cycle"}, notes="the export is set before the require: node reaches a.x, and ripple still refuses a cycle.")

# F: a class reached through a CommonJS export draws no member edge
probe("r1-c12-static-method-replaces-the-static-through-this", {"lib/c.js": "class C {\n  static make() { return 'lib/c:make'; }\n  static boot() { this.make = function () { return 'patched'; }; }\n}\nmodule.exports = C;\n",
      M: "const C = require('./lib/c');\nC.boot();\n" + s(1, "C.make()")}, refuse={f"{M}:s1": "class"}, notes="c12.")
probe("r1-c13-instance-constructor-replaces-the-static", {"lib/c.js": "class C {\n  static make() { return 'lib/c:make'; }\n}\nmodule.exports = C;\n",
      "lib/patch.js": "const C = require('./c');\nnew C().constructor.make = " + R + ";\n", M: "require('./lib/patch');\nconst C = require('./lib/c');\n" + s(1, "C.make()")},
      refuse={f"{M}:s1": "constructor"}, notes="c13.")
probe("f-static-block-replaces-the-static", {"lib/c.js": "class C {\n  static make() { return 'lib/c:make'; }\n  static { this.make = function () { return 'patched'; }; }\n}\nmodule.exports = C;\n",
      M: "const C = require('./lib/c');\n" + s(1, "C.make()")}, refuse={f"{M}:s1": "class"}, notes="a static block runs with this = the class.")
probe("f-prototype-replaced-in-another-file", {"lib/c.js": "class C { run() { return 'lib/c:C.run'; } }\nmodule.exports = C;\n",
      "lib/patch.js": "const C = require('./c');\nC.prototype.run = " + R + ";\n", M: "require('./lib/patch');\nconst C = require('./lib/c');\n" + s(1, "new C().run()")},
      refuse={f"{M}:s1": "open"}, notes="an instance method replaced on the prototype of the class another file exports.")
probe("f-class-in-an-object-export", {"lib/c.js": "class C { static make() { return 'lib/c:make'; } }\nmodule.exports = { C };\n",
      "lib/patch.js": "const { C } = require('./c');\nC.make = " + R + ";\n", M: "require('./lib/patch');\nconst { C } = require('./lib/c');\n" + s(1, "C.make()")},
      refuse={f"{M}:s1": "class"}, notes="a class held as a member of the exports.")
probe("f-class-exported-by-property", {"lib/c.js": "class C { static make() { return 'lib/c:make'; } }\nexports.C = C;\n", M: "const { C } = require('./lib/c');\n" + s(1, "C.make()")},
      refuse={f"{M}:s1": "class"}, notes="no member edge through a class, even with nothing that writes it.")

# G: one module, one file
patched("r1-c07-symlinked-file", "require('./b').helper = %R%;\n", links={"lib/b.js": "a.js"}, notes="c7: node keys the cache by realpath: b is a.", part="symbolic link")
patched("g-symlinked-directory", "require('./lib2/a').helper = %R%;\n", patch_file="patch.js", main="require('./patch');\n" + MAIN_A + s(1, CALL_A), links={"lib2": "lib"}, part="open",
        notes="lib2 is lib, and the walk does not enter it.")

# H: an absolute path and a directory named like `..` are no package
probe("r1-c17-absolute-path-require", {"lib/a.js": LIB_A, M: "const a = require('/tmp/ripple-cjs-probes/r1-c17-absolute-path-require/lib/a');\n" + s(1, CALL_A)},
      refuse={f"{M}:s1": "absolute"}, notes="c17: node reaches the repo file by its absolute path.")
probe("r1-c17-es-import-absolute-path", {"lib/a.mjs": "export function helper() { return 'lib/a:helper'; }\n",
      "m.mjs": "import { helper } from '/tmp/ripple-cjs-probes/r1-c17-es-import-absolute-path/lib/a.mjs';\n" + esm_s(1, "helper()")}, entries=("m.mjs",),
      refuse={"m.mjs:s1": "absolute"}, notes="c17 on the ES import path: an absolute path was a package there too.")
probe("r1-c18-directory-named-dot-dot-x", {"..x/a.js": "function helper() { return '..x/a:helper'; }\nmodule.exports = { helper };\n", M: "const a = require('./..x/a');\n" + s(1, CALL_A)},
      notes="c18: ..x is a directory of the repo, not a path above it.")
patched("h-mock-with-a-computed-specifier", "const jest = { mock() {} };\njest.mock(require.resolve('./a'), () => ({ helper: () => 'mocked' }));\n", part="mock",
        notes="a test framework replaces the module the call names: here a no-op stand-in, so node still reaches helper.")
patched("h-mock-with-a-joined-path", "const path = require('path');\nconst vi = { mock() {} };\nvi.mock(path.join(__dirname, 'a'), () => ({}));\n", part="mock")
patched("h-mock-with-a-literal-opens-that-file", "const jest = { mock() {} };\njest.mock('./a');\n", part="mock")
probe("h-compiler-and-node-name-different-files", {"lib/a.js": LIB_A, "lib/a.mjs": "export function helper() { return 'lib/a.mjs:helper'; }\n", M: MAIN_A + s(1, CALL_A)},
      refuse={f"{M}:s1": "compiler"}, notes="require('./lib/a') is a.js to node and may be a.mjs to a bundler: which one is not known.")


# the cases the review found held (c9, c10, c16): they stay what they were, unresolved or not followed, never a wrong edge
probe("r1-c09-workspace-symlink", {"package.json": "{\"name\":\"root\",\"private\":true,\"workspaces\":[\"packages/*\"]}\n", "packages/pkg/package.json": "{\"name\":\"pkg\",\"main\":\"index.js\"}\n",
      "packages/pkg/index.js": "function helper() { return 'packages/pkg:helper'; }\nmodule.exports = { helper };\n", M: "const p = require('pkg');\n" + s(1, "p.helper()")},
      links={"node_modules/pkg": "../packages/pkg"}, notes="c9: a workspace package linked into node_modules is repo code.")
probe("r1-c10-self-reference-and-imports", {"package.json": "{\"name\":\"selfpkg\",\"exports\":{\".\":\"./lib/a.js\",\"./x\":\"./lib/a.js\"}}\n", "lib/a.js": LIB_A,
      M: "const p = require('selfpkg/x');\n" + s(1, "p.helper()") + "exports.s2 = function () { const q = require('#int'); return q.helper(); }; // site s2\n"},
      notes="c10: a package name that is the repo's own, and a #import no package.json defines.")
probe("r1-c16-type-that-is-not-a-string", {"p/package.json": "{\"type\":5}\n", "p/x.js": "exports.x = 1;\n", M: "exports.s1 = function () { return require('./p/x'); }; // site s1\n"},
      notes="c16: node throws on a type that is not a string, so what the file would be is not known.")

# ---- the review of the second CommonJS model (round 2): each case as node ran it (the directories b1 to d8, k0, m1, n, r, s, t) -----
# W is the function the patch installs. The `a.boot();` cases call a function the exports hold, as a member, before the site.
W = "function () { return 'patched'; }"


def p2(name, patch, part="", notes="", extra=None, main=None, patch_file="lib/patch.js", edge=(), links=None, refuse=True, entries=("main.js",)):
    """Like patched(), with `part` optional: refuse=False is a case node ran and ripple may keep the edge for (or leave unresolved)."""
    patched(name, patch, part=part or "open", notes=notes, extra=extra, main=main, patch_file=patch_file, edge=edge, links=links, refuse=refuse, entries=entries)


BOOT = "const a = require('./lib/a');\na.boot();\n" + s(1, CALL_A)


def boot(name, a_js, extra=None, part="open", notes="", main=None, edge=(), refuse=True):
    """lib/a.js exports helper and something else; main calls `a.boot()` (a member call that runs with this = the exports) and then helper."""
    files = {"lib/a.js": a_js, M: main or BOOT}
    files.update(extra or {})
    probe(name, files, edge=edge, refuse={f"{M}:s1": part} if refuse else {}, notes=notes)


HELPER = "function helper() { return 'lib/a:helper'; }\n"
BOOT_W = "function () { this.helper = " + W + "; }"
# 1: who can write the exports through `this`
boot("r2-t1-var-assigned-later", HELPER + "var boot;\nboot = " + BOOT_W + ";\nmodule.exports = { helper, boot };\n", notes="t1: a function assigned to a var after its declaration, exported by name.")
boot("r2-t2-factory", HELPER + "function make() { return " + BOOT_W + "; }\nconst boot = make();\nmodule.exports = { helper, boot };\n", notes="t2: a function a call made.")
boot("r2-t3-from-other-file", HELPER + "exports.helper = helper;\nexports.boot = require('./b').boot;\n", extra={"lib/b.js": "exports.boot = " + BOOT_W + ";\n"}, notes="t3: a function borrowed from another file.")
boot("r2-t4-getter", HELPER + "module.exports = { helper, get boot() { this.helper = " + W + "; return 1; } };\n", main="const a = require('./lib/a');\nif (a.boot) {}\n" + s(1, CALL_A), notes="t4: a getter that writes the exports, run by a read.")
boot("r2-t5-ternary", HELPER + "exports.helper = helper;\nexports.boot = true ? " + BOOT_W + " : null;\n", notes="t5: a conditional that holds a function.")
boot("r2-t6-method-of-a-class", HELPER + "class B { boot() { this.helper = " + W + "; } }\nexports.helper = helper;\nexports.boot = B.prototype.boot;\n", notes="t6: a class method taken off the prototype.")
boot("r2-r2-loose-equality-runs-tostring", HELPER + "exports.helper = helper;\nexports.toString = require('./b').ts;\n", main="const a = require('./lib/a');\nif (a == 'x') {}\n" + s(1, CALL_A),
     extra={"lib/b.js": "exports.ts = function () { this.helper = " + W + "; return 'x'; };\n"}, notes="r2: `a == 'x'` calls the exports' toString with this = the exports.")
boot("r2-r1-unexported-top-level", HELPER + "module.exports = { helper };\n", main="const a = require('./lib/a');\nexports.s1 = function () { return a.secret(); }; // site s1\n", part="not exported", notes="r1: a member the exports do not define.")
# 2: routes to the loader
p2("r2-d1-process-on", "process.on('x', () => {}).getBuiltinModule('module').createRequire(__filename)('./a').helper = %R%;\n".replace("%R%", W), part="process", notes="d1: on() returns process.")
p2("r2-d1b-process-once", "process.once('x', () => {}).getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n", part="process", notes="d1b: once() returns process.")
p2("r2-d2-top-level-arguments", "arguments[1]('./a').helper = " + W + ";\n", part="arguments", notes="d2: arguments[1] is the wrapper's require.")
p2("r2-d2b-arguments-in-an-arrow", "const r = (() => arguments[1])();\nr('./a').helper = " + W + ";\n", part="arguments", notes="d2b: an arrow function has no arguments of its own.")
p2("r2-d3-sloppy-this", "const g = (function () { return this; })();\ng.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n", part="this", notes="d3: a sloppy function called bare has this = globalThis.")
p2("r2-d4-caller", "function g() { return g.caller.arguments[1]; }\ng()('./a').helper = " + W + ";\n", part="caller", notes="d4: g.caller is the module wrapper.")
p2("r2-d5-shorthand-constructor", "const { constructor } = function () {};\nconstructor('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n", part="constructor", notes="d5.")
p2("r2-d6-reflect-get-constructor", "const F = Reflect.get(function () {}, 'constructor');\nF('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n", part="Reflect", notes="d6.")
p2("r2-d7-escaped-key", "const F = (function () {})['\\x63onstructor'];\nF('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n", part="constructor", notes="d7: a string with an escape.")
p2("r2-d8-template-key", "const F = (function () {})[`constructor`];\nF('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n", part="constructor", notes="d8: a template with no substitution.")
p2("r2-r4-process-stdout-write", "process.stdout.write('');\n", refuse=False, edge=[f"{M}:s1"], notes="r4: a harmless process member stays harmless.")
p2("r2-r5-global-require", "global.require = function () { return { helper: " + W + " }; };\n", refuse=False, notes="r5: harmless to a file that already has its own require.")
p2("r2-r6-module-id", "const x = module.id + String(require.main === undefined ? '' : require.main.filename);\n", refuse=False, edge=[f"{M}:s1"], notes="r6.")
# 4 and 5: a require of a file ripple does not read
p2("r2-c1-computed-relative-hidden", "", patch_file=".hidden/patch.js", part="computed", notes="c1: require('./' + name) runs a file in a hidden directory.",
   main="const a = require('./lib/a');\nconst name = '.hidden/patch';\nrequire('./' + name);\n" + s(1, CALL_A), extra={".hidden/patch.js": "require('../lib/a').helper = " + W + ";\n"})
p2("r2-c2-computed-relative-node-modules", "", part="computed", notes="c2: a template that names a package in node_modules.",
   main="const a = require('./lib/a');\nconst name = 'pkg';\nrequire(`./node_modules/${name}`);\n" + s(1, CALL_A), extra={"node_modules/pkg/index.js": "require('../../lib/a').helper = " + W + ";\n"})
p2("r2-c3-computed-relative-txt", "", part="computed", notes="c3: the extension is computed, and the file is a .txt.",
   main="const a = require('./lib/a');\nconst ext = '.txt';\nrequire('./lib/patch' + ext);\n" + s(1, CALL_A), extra={"lib/patch.txt": "require('./a').helper = " + W + ";\n"})
p2("r2-n1-relative-into-node-modules", "require('../node_modules/pkg');\n", part="does not read", notes="n1: a relative path into node_modules.", extra={"node_modules/pkg/index.js": "require('../../lib/a').helper = " + W + ";\n"})
p2("r2-n2-relative-into-bower-components", "require('./bower_components/x');\n", part="does not read", notes="n2.", extra={"lib/bower_components/x/index.js": "require('../../a').helper = " + W + ";\n"})
p2("r2-k0-control", "require('./a').helper = " + W + ";\n", part="another file", notes="k0: the plain write through a require.")
p2("r2-s1-directory-symlink", "require('../lib2/a').helper = " + W + ";\n", part="open", notes="s1.", links={"lib2": "lib"})
probe("r2-r3-destructure-then-write-nested", {"lib/a.js": LIB_A, "lib/idx.js": "module.exports = { helper: require('./a').helper };\n", "lib/patch.js": "const { helper } = require('./a');\nhelper.x = 1;\n", M: "require('./lib/patch');\nconst idx = require('./lib/idx');\n" + MAIN_A + s(1, CALL_A)},
      notes="r3: a write below a destructured member changes the function, not the exports.", edge=[])
# 6: the binding a call goes through is bound once
LET_A = "let a = require('./lib/a');\n"
probe("r2-b1-rebound-in-a-function", {"lib/a.js": LIB_A, M: LET_A + "function swap() { a = { helper() { return 'local'; } }; }\nswap();\n" + s(1, CALL_A)}, refuse={f"{M}:s1": "more than once"}, notes="b1.")
probe("r2-b2-var-twice", {"lib/a.js": LIB_A, M: "var a = require('./lib/a');\nvar a = { helper() { return 'local'; } };\n" + s(1, CALL_A)}, refuse={f"{M}:s1": "more than once"}, notes="b2.")
probe("r2-b3-catch-shadow", {"lib/a.js": LIB_A, M: MAIN_A + "try { throw { helper() { return 'x'; } }; } catch (a) { a.helper = 1; }\n" + s(1, CALL_A)}, refuse={f"{M}:s1": "more than once"}, notes="b3: a catch parameter named like the binding.")
probe("r2-b4-for-of-rebind", {"lib/a.js": LIB_A, M: "var a = require('./lib/a');\nfor (a of [{ helper() { return 'local'; } }]) {}\n" + s(1, CALL_A)}, refuse={f"{M}:s1": "more than once"}, notes="b4.")
probe("r2-b5-destructuring-assignment", {"lib/a.js": LIB_A, M: "var a = require('./lib/a');\n[a] = [{ helper() { return 'local'; } }];\n" + s(1, CALL_A)}, refuse={f"{M}:s1": "more than once"}, notes="b5.")
probe("r2-b6-annex-b-block-function-named-like-the-require", {"lib/a.js": LIB_A, M: "var a = require('./lib/a');\n{ function a() {} }\n" + s(1, CALL_A)}, refuse={f"{M}:s1": "more than once"}, notes="b6: node throws, a is the block function.")
probe("r2-b7-annex-b-destructured", {"lib/a.js": LIB_A, M: "var { helper } = require('./lib/a');\n{ function helper() { return 'local'; } }\n" + s(1, "helper()")}, refuse={f"{M}:s1": "more than once"}, notes="b7: the regression against main.")
probe("r2-b8-annex-b-in-an-if", {"lib/a.js": LIB_A, M: "var a = require('./lib/a');\nif (true) { function a() {} }\n" + s(1, CALL_A)}, refuse={f"{M}:s1": "more than once"}, notes="b8.")
probe("r2-b9-annex-b-over-a-local-function", {M: "function helper() { return 'main:helper'; }\n{ function helper() { return 'main:helper2'; } }\n" + s(1, "helper()")}, refuse={f"{M}:s1": "more than one way"},
      notes="b9: the block function is assigned to the top-level name when the block runs (sloppy mode): not a require, the same hole.")
# the layout the reviewer reached the last return of resolve_require with
probe("r2-m1-node-and-the-compiler-name-different-files", {"x.cjs/index.js": "function f() { return 'x.cjs/index.js:f'; }\nexports.f = f;\n", "x.cjs.jsx": "exports.f = function () { return 'x.cjs.jsx'; };\n",
      M: "const x = require('./x.cjs');\n" + s(1, "x.f()")}, refuse={f"{M}:s1": "compiler"}, notes="m1: Node names x.cjs/index.js (no .jsx is tried), the compiler names x.cjs.jsx: which file is not known.")


# ---- round 2, enumerated: the other members of each class the review found, each with node's answer ---------------------------------
LOAD = ".getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n"  # from a value that is `process`: patch lib/a
PATCHER = "function () { this.helper = " + W + "; }"
STRICT = "'use strict';\n"

# 1: every kind of expression that can be an exported value. `a.boot()` runs it with this = the exports; main then calls helper.
boot("e1-identifier-of-a-function-using-this", HELPER + "const fn = " + PATCHER + ";\nexports.helper = helper;\nexports.boot = fn;\n", part="this")
boot("e1-identifier-destructured-from-a-require", HELPER + "const { boot } = require('./b');\nexports.helper = helper;\nexports.boot = boot;\n", extra={"lib/b.js": "exports.boot = " + PATCHER + ";\n"}, part="this")
boot("e1-iife-result", HELPER + "exports.helper = helper;\nexports.boot = (function () { return " + PATCHER + "; })();\n", part="this")
boot("e1-member-of-an-object", HELPER + "const holder = { boot: " + PATCHER + " };\nexports.helper = helper;\nexports.boot = holder.boot;\n", part="this")
boot("e1-subscript-of-an-object", HELPER + "const holder = { boot: " + PATCHER + " };\nexports.helper = helper;\nexports.boot = holder['boot'];\n", part="this")
boot("e1-logical-or", HELPER + "exports.helper = helper;\nexports.boot = null || " + PATCHER + ";\n", part="this")
boot("e1-nullish", HELPER + "exports.helper = helper;\nexports.boot = null ?? " + PATCHER + ";\n", part="this")
boot("e1-logical-and", HELPER + "exports.helper = helper;\nexports.boot = true && " + PATCHER + ";\n", part="this")
boot("e1-sequence", HELPER + "exports.helper = helper;\nexports.boot = (0, " + PATCHER + ");\n", part="this")
boot("e1-bound-function", HELPER + "exports.helper = helper;\nexports.boot = (" + PATCHER + ").bind(undefined);\n", part="this",
     notes="bind() fixes this: node reaches helper. The value is a call result, which opens the exports all the same.")
boot("e1-arrow-with-the-module-this", HELPER + "exports.helper = helper;\nexports.boot = () => { this.helper = " + W + "; };\n", part="this")
boot("e1-nested-arrow-inside-a-function", HELPER + "exports.helper = helper;\nexports.boot = function () { const f = () => { this.helper = " + W + "; }; f(); };\n", part="this")
boot("e1-method-with-super", HELPER + "module.exports = { helper, boot() { super.helper = " + W + "; } };\n", part="this", notes="super.x = v sets x on this.")
boot("e1-generator", HELPER + "exports.helper = helper;\nexports.boot = function* () { this.helper = " + W + "; };\n", main="const a = require('./lib/a');\na.boot().next();\n" + s(1, CALL_A), part="this")
boot("e1-async-function", HELPER + "exports.helper = helper;\nexports.boot = async function () { this.helper = " + W + "; };\n", part="this", notes="the body runs to its first await at the call.")
boot("e1-setter", HELPER + "module.exports = { helper, set boot(v) { this.helper = " + W + "; } };\n", main="const a = require('./lib/a');\na.boot = 1;\n" + s(1, CALL_A), part="this")
boot("e1-to-primitive-computed-key", HELPER + "module.exports = { helper, [Symbol.toPrimitive]() { this.helper = " + W + "; return 1; } };\n", main="const a = require('./lib/a');\nif (+a) {}\n" + s(1, CALL_A), part="computed")
boot("e1-value-of-method-with-relational-compare", HELPER + "module.exports = { helper, valueOf() { this.helper = " + W + "; return 1; } };\n", main="const a = require('./lib/a');\nif (a < 2) {}\n" + s(1, CALL_A), part="this")
boot("e1-to-string-method-in-a-template", HELPER + "module.exports = { helper, toString() { this.helper = " + W + "; return 'x'; } };\n", main="const a = require('./lib/a');\nconst t = `${a}`;\n" + s(1, CALL_A), part="this")
boot("e1-to-string-method-in-a-concatenation", HELPER + "module.exports = { helper, toString() { this.helper = " + W + "; return 'x'; } };\n", main="const a = require('./lib/a');\nconst t = a + '';\n" + s(1, CALL_A), part="this")
boot("e1-to-string-method-in-an-in-test", HELPER + "module.exports = { helper, toString() { this.helper = " + W + "; return 'x'; } };\n", main="const a = require('./lib/a');\nconst t = a in { x: 1 };\n" + s(1, CALL_A), part="this")
boot("e1-to-string-method-in-loose-inequality", HELPER + "module.exports = { helper, toString() { this.helper = " + W + "; return 'x'; } };\n", main="const a = require('./lib/a');\nif (a != 'y') {}\n" + s(1, CALL_A), part="this")
boot("e1-late-property-from-another-file", HELPER + "module.exports = { helper };\nmodule.exports.boot = require('./b').boot;\n", extra={"lib/b.js": "exports.boot = " + PATCHER + ";\n"}, part="this")
boot("e1-class-expression-value", HELPER + "exports.helper = helper;\nexports.K = class {};\n", main=MAIN_A + s(1, CALL_A), part="class", notes="harmless in node, but a class is a value whose members run with this.")
boot("e1-object-value", HELPER + "exports.helper = helper;\nexports.o = { n: 1 };\n", main=MAIN_A + s(1, CALL_A), part="object", notes="harmless in node: another object, whose methods have their own this.")
boot("e1-array-value", HELPER + "exports.helper = helper;\nexports.list = [1];\n", main=MAIN_A + s(1, CALL_A), part="this")
boot("e1-whole-exports-of-another-file", HELPER + "exports.helper = helper;\nexports.b = require('./b');\n", extra={"lib/b.js": "exports.boot = " + PATCHER + ";\n"}, main=MAIN_A + s(1, CALL_A), part="this")
boot("e1-new-expression-value", HELPER + "exports.helper = helper;\nexports.inst = new (class P { toString() { this.n = 1; return 'x'; } })();\n", main=MAIN_A + s(1, CALL_A), part="this")
boot("e1-name-bound-by-assignment-to-an-earlier-function", HELPER + "function early() {}\nlet boot = early;\nboot = " + PATCHER + ";\nexports.helper = helper;\nexports.boot = boot;\n", part="this")
boot("e1-declared-twice-second-uses-this", "function helper() { return 'lib/a:helper'; }\nfunction boot() {}\nfunction boot() { this.helper = " + W + "; }\nexports.helper = helper;\nexports.boot = boot;\n", part="this")
boot("e1-this-in-a-default-parameter", HELPER + "exports.helper = helper;\nexports.boot = function (x = (this.helper = " + W + ")) {};\n", part="this", notes="a default is evaluated with the function's this.")
boot("e1-this-in-a-destructured-default", HELPER + "exports.helper = helper;\nexports.boot = function ({ x = (this.helper = " + W + ") } = {}) {};\n", part="this")
boot("e1-returned-arrow-takes-the-this-of-the-call", HELPER + "exports.helper = helper;\nexports.boot = function () { return () => { this.helper = " + W + "; }; };\n", main="const a = require('./lib/a');\na.boot()();\n" + s(1, CALL_A), part="this")
boot("e1-function-returns-this-and-the-consumer-writes-through-it", HELPER + "exports.helper = helper;\nexports.me = function () { return this; };\n", main="const a = require('./lib/a');\na.me().helper = " + W + ";\n" + s(1, CALL_A), part="this")
boot("e1-function-returns-the-exports-by-name", HELPER + "exports.helper = helper;\nexports.me = function () { return exports; };\n", main="const a = require('./lib/a');\na.me().helper = " + W + ";\n" + s(1, CALL_A), part="exports")
probe("e1-primitives-and-plain-functions-stay-closed", {"lib/a.js": HELPER + "function other() { return 'lib/a:other'; }\nconst arrow = () => 1;\nmodule.exports = { helper, other, n: 1, neg: -1, s: 'x', t: `y`, yes: true, no: false, nothing: null, arrow, inline: function () { return 2; }, method() { return 3; } };\n",
      M: MAIN_A + s(1, CALL_A) + s(2, "a.other()")}, edge=[f"{M}:s1", f"{M}:s2"], notes="every own property is a primitive or a function written here with no this: closed.")
probe("e1-late-primitive-property-stays-closed", {"lib/a.js": HELPER + "module.exports = { helper };\nmodule.exports.version = 3;\n", M: MAIN_A + s(1, CALL_A)}, edge=[f"{M}:s1"],
      notes="exports.name = 'x' goes to the old object (dead alias) when module.exports was replaced: refused for that, and the late property by module.exports is a primitive.", )
# the consumer side: every comparison and coercion of the value a require gave
for n, form in enumerate(["a == 'x'", "a != 'x'", "a < 1", "a + ''", "`${a}`", "'x' in a", "1 instanceof a", "+a", "-a", "a * 2", "String(a)", "[a].join()", "a ?? 1", "a || 1", "a && 1"], 1):
    probe(f"e1-consumer-{n:02d}",
          {"lib/a.js": HELPER + "module.exports = { helper };\n", "lib/patch.js": "const a = require('./a');\ntry { const r = " + form + "; } catch (e) {}\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, CALL_A)},
          refuse={f"{M}:s1": "another file"}, notes=f"`{form}` is a use of the exports object that is no ===, !==, typeof, !, void or test: it can call a hook (toString, valueOf, Symbol.toPrimitive) or keep the object.")
for n, form in enumerate(["a === 1", "a !== 1", "typeof a", "!a", "void a", "a ? 1 : 2", "a === a"], 1):
    probe(f"e1-consumer-allowed-{n:02d}", {"lib/a.js": HELPER + "module.exports = { helper };\n", "lib/patch.js": "const a = require('./a');\nconst r = " + form + ";\n", M: "require('./lib/patch');\n" + MAIN_A + s(1, CALL_A)},
          edge=[f"{M}:s1"], notes=f"`{form}` calls no hook on the object.")

# 2: routes to the loader. A function called with no receiver in sloppy code has the global object as `this`.
THIS_PROCESS = "G.process" + LOAD
loader = lambda name, body, part, notes="", **kw: p2(name, body, part=part, notes=notes, **kw)  # noqa: E731
loader("e2-this-function-declaration", "function g() { return this; }\nconst G = g();\n" + THIS_PROCESS, "this")
loader("e2-this-arrow-inside-a-function", "const G = (function () { return (() => this)(); })();\n" + THIS_PROCESS, "this")
loader("e2-this-object-method-called-bare", "const m = { f() { return this; } }.f;\nconst G = m();\n" + THIS_PROCESS, "this")
loader("e2-this-generator", "function* gen() { return this; }\nconst G = gen().next().value;\n" + THIS_PROCESS, "this")
loader("e2-this-array-callback", "const G = [1].map(function () { return this; })[0];\n" + THIS_PROCESS, "this")
loader("e2-this-function-expression-named", "const G = (function named() { return this; })();\n" + THIS_PROCESS, "this")
loader("e2-this-call-with-undefined", "function g() { return this; }\nconst G = g.call(undefined);\n" + THIS_PROCESS, "this")
loader("e2-this-in-a-function-with-an-inner-strict-one", "const G = (function () { return (function () { 'use strict'; return 1; })() && this; })();\n" + THIS_PROCESS, "this",
       notes="the directive of the inner function does not make the outer one strict.")
for n, (kind, code) in enumerate([("declaration", "function g() { return this; }\nconst G = g();"), ("expression", "const G = (function () { return this; })();"), ("arrow-in-function", "const G = (function () { return (() => this)(); })();"),
                                  ("method", "const m = { f() { return this; } }.f;\nconst G = m();")], 1):
    p2(f"e2-strict-file-this-{n}-{kind}", STRICT + code + "\n", refuse=False, edge=[f"{M}:s1"], notes="the file is strict: a function called bare has undefined as this, so no loader is reached.")
    p2(f"e2-sloppy-file-this-{n}-{kind}", code + "\n", part="this", notes="the same in a file with no directive: the global object, which holds process. node reaches helper here (nothing uses it), and ripple still refuses.")
p2("e2-strict-function-this", "function g() { 'use strict'; return this; }\ng();\n", refuse=False, edge=[f"{M}:s1"], notes="a function with its own directive.")
p2("e2-this-in-a-class-body", "class K { m() { return this; } static s() { return this; } }\nnew K().m();\nK.s();\n", refuse=False, edge=[f"{M}:s1"], notes="a class body is strict.")
loader("e2-caller-of-a-function-called-from-the-top", "function g() { return arguments.callee.caller; }\nconst w = g();\nw.arguments[1]('./a').helper = " + W + ";\n", "caller")
loader("e2-caller-arguments", "function g() { return g.caller.arguments[2]; }\ng().require('./a').helper = " + W + ";\n", "caller")
loader("e2-arguments-destructured", "const [, req] = arguments;\nreq('./a').helper = " + W + ";\n", "arguments")
loader("e2-arguments-slice", "Array.prototype.slice.call(arguments)[1]('./a').helper = " + W + ";\n", "arguments")
loader("e2-arguments-spread-module-require", "[...arguments][2].require('./a').helper = " + W + ";\n", "arguments")
loader("e2-arguments-in-a-nested-arrow-chain", "const r = (() => (() => arguments[1])())();\nr('./a').helper = " + W + ";\n", "arguments")
p2("e2-arguments-of-a-function-stays-harmless", "function f() { return arguments.length + arguments[0]; }\nf(1);\n", refuse=False, edge=[f"{M}:s1"], notes="a function's own arguments.")
for n, (name, code) in enumerate([("assigned", "const p = process.on('x', () => {});\np.getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n"),
                                  ("in-an-array", "[process.on('x', () => {})][0].getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n"),
                                  ("returned-by-a-function", "function reg() { return process.on('x', () => {}); }\nreg().getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n"),
                                  ("returned-by-an-arrow", "const reg = () => process.once('x', () => {});\nreg().getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n"),
                                  ("off", "process.off('x', () => {}).getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n"),
                                  ("parenthesized", "(process.on('x', () => {})).getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n"),
                                  ("as-an-argument", "const use = (p) => p.getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\nuse(process.once('x', () => {}));\n"),
                                  ("ternary", "const p = true ? process.on('x', () => {}) : null;\np.getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n"),
                                  ("sequence", "(0, process.on('x', () => {})).getBuiltinModule('module').createRequire(__filename)('./a').helper = " + W + ";\n")], 1):
    loader(f"e2-process-on-result-{n}-{name}", code, "process")
p2("e2-process-on-statement-stays-harmless", "process.on('exit', function () {});\nprocess.once('exit', () => {});\nprocess.off('exit', function () {});\nprocess.stdout.on('error', function () {}).write('');\nprocess.nextTick(function () {});\nconst n = process.cwd().length + process.hrtime().length;\n", refuse=False, edge=[f"{M}:s1"],
   notes="a listener call as a statement of its own, and the members whose result is no process.")

# 3: the names, in every spelling and position
CTOR_CODE = [
    ("subscript-string", "const F = (function () {})['constructor'];"), ("optional-member", "const F = (function () {})?.constructor;"), ("optional-subscript", "const F = (function () {})?.['constructor'];"),
    ("destructure-rename", "const { constructor: F } = function () {};"), ("destructure-string-key", "const { 'constructor': F } = function () {};"),
    ("destructure-computed-literal-key", "const { ['constructor']: F } = function () {};"), ("destructure-default", "const { constructor: F = null } = function () {};"),
    ("destructure-assignment", "let F;\n({ constructor: F } = function () {});"), ("destructure-assignment-shorthand", "let constructor;\n({ constructor } = function () {});\nconst F = constructor;"),
    ("destructure-parameter", "function get({ constructor }) { return constructor; }\nconst F = get(function () {});"), ("unicode-escaped-identifier", "const F = (function () {}).c\\u006fnstructor;"),
    ("braced-unicode-escape", "const F = (function () {}).\\u{63}onstructor;"), ("unicode-escaped-string", "const F = (function () {})['\\u0063onstructor'];"),
    ("template-with-escape", "const F = (function () {})[`\\x63onstructor`];"), ("octal-free-line-continuation", "const F = (function () {})['con\\\nstructor'];"),
    ("descriptor-value", "const F = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(function () {}), 'constructor').value;"),
    ("entries-of-descriptors", "const F = Object.entries(Object.getOwnPropertyDescriptors(Object.getPrototypeOf(function () {}))).find((e) => e[0] === 'constructor')[1].value;"),
    ("function-prototype-constructor", "const F = Function.prototype.constructor;"), ("proto-constructor", "const F = (function () {}).__proto__.constructor;"),
    ("reflect-get-template", "const F = Reflect.get(function () {}, `constructor`);"), ("reflect-own-descriptor", "const F = Reflect.getOwnPropertyDescriptor(Reflect.getPrototypeOf(function () {}), 'constructor').value;"),
    ("string-held-in-a-variable", "const k = 'constructor';\nconst F = (function () {})[k];"), ("string-in-an-array-lookup", "const F = (function () {})[['constructor'][0]];"),
]
for name, code in CTOR_CODE:
    loader(f"e3-constructor-{name}", code + "\nF('return process')()" + LOAD, "constructor" if name not in ("reflect-get-template", "reflect-own-descriptor", "string-held-in-a-variable", "string-in-an-array-lookup", "function-prototype-constructor", "proto-constructor", "descriptor-value", "entries-of-descriptors") else "")
# the names that hand out the loader or the prototype every object inherits from
POISON = "function () { this.helper = " + W + "; }"
OBJPROTO = [("object-prototype", "Object.prototype.boot = " + POISON + ";"), ("dunder-proto", "({}).__proto__.boot = " + POISON + ";"), ("get-prototype-of", "Object.getPrototypeOf({}).boot = " + POISON + ";"),
            ("constructor-prototype", "({}).constructor.prototype.boot = " + POISON + ";"), ("define-property", "Object.defineProperty(Object.prototype, 'boot', { value: " + POISON + ", configurable: true });"),
            ("assign", "Object.assign(Object.prototype, { boot: " + POISON + " });"), ("alias-of-object", "const O = Object;\nO.prototype.boot = " + POISON + ";"),
            ("reflect-get-prototype-of", "Reflect.getPrototypeOf({}).boot = " + POISON + ";"), ("prototype-of-a-prototype", "class X {}\nObject.getPrototypeOf(X.prototype).boot = " + POISON + ";"),
            ("define-getter", "Object.prototype.__defineGetter__('boot', function () { return " + POISON + "; });"), ("parameter-holds-object", "function poison(o) { o.prototype.boot = " + POISON + "; }\npoison(Object);"),
            ("variable-holds-the-prototype", "const proto = Object.prototype;\nproto.boot = " + POISON + ";"), ("bracket-prototype", "Object['prototype'].boot = " + POISON + ";"),
            ("escaped-prototype", "Object.proto\\u0074ype.boot = " + POISON + ";"), ("proto-of-an-array", "[].__proto__.__proto__.boot = " + POISON + ";"),
            ("set-prototype-chain", "const child = Object.create(null);\nObject.setPrototypeOf(child, Object.prototype);\nObject.getPrototypeOf(child).boot = " + POISON + ";")]
for name, code in OBJPROTO:
    probe(f"e3-poisoned-{name}", {"lib/a.js": LIB_A, "lib/patch.js": code + "\n", M: "require('./lib/patch');\n" + MAIN_A + "a.boot();\n" + s(1, CALL_A)}, refuse={f"{M}:s1": "open"},
          notes="a method put on Object.prototype is a member every exports object has: a.boot() reaches it and writes the exports.")
probe("e3-prototype-forms-that-keep-the-edge", {"lib/a.js": LIB_A, "lib/patch.js": "const EE = require('events');\nfunction K() {}\nK.prototype.m = function () {};\nclass C { }\nC.prototype.n = function () {};\nconst viaK = Object.create(K.prototype);\nconst viaEE = Object.create(EE.prototype);\nconst viaArray = Object.create(Array.prototype);\nconst has = Object.prototype.hasOwnProperty.call({}, 'k');\nconst slice = Array.prototype.slice.call([1]);\nK.prototype.constructor = K;\nconst o = { constructor: K, prototype: 1, 'process': 2 };\nclass D { constructor() { this.x = 1; } }\nexports.keys = [viaK, viaEE, viaArray, has, slice, o, D];\n",
      M: "require('./lib/patch');\n" + MAIN_A + s(1, CALL_A)}, edge=[f"{M}:s1"], notes="the prototype of a local function or class, of a package's class or a global class that is no Object; a method of a prototype read; a defined key.")
for name, code in [("hex-escape-spec", "require('v\\x6d').runInThisContext(\"process.getBuiltinModule('module').createRequire(process.cwd() + '/x.js')('./lib/a').helper = function () { return 'patched'; }\");\n"),
                   ("template-spec", "require(`node:vm`).runInThisContext(\"process.getBuiltinModule('module').createRequire(process.cwd() + '/x.js')('./lib/a').helper = function () { return 'patched'; }\");\n")]:
    loader(f"e3-{name}", code, "")

loader("e3-stack-trace-hooks", "Error.prepareStackTrace = (e, frames) => frames;\nconst frame = new Error().stack[0];\nconst own = frame.getThis();\nframe.getFunction().arguments[1]('./a').helper = " + W + ";\n", "prepareStackTrace",
       notes="a CallSite of a sloppy function gives its function (and so its arguments, the wrapper's require) and its this.")
loader("e3-dlopen-and-binding", "try { process.dlopen({}, '/nonexistent.node'); } catch (e) {}\ntry { process.binding('natives'); } catch (e) {}\n", "process", refuse=True,
       notes="the loader names process.dlopen and process.binding: nothing patched here, ripple still reflects.")
loader("e3-shorthand-property-named-constructor", "const constructor = 1;\nconst o = { constructor };\nexports.o = o;\n", "constructor", notes="a variable named constructor held by a shorthand: node patches nothing, the name is spelled in a node kind of its own.")
loader("e3-proxy-and-reflect-wrap-a-write", "const target = {};\nconst proxy = new Proxy(target, {});\nReflect.set(proxy, 'x', 1);\n", "Proxy", notes="Proxy and Reflect are named: they reflect outright.")
# a key spelled with an escape is the name it decodes to, wherever a name is compared
SECOND = "function second() { return 'lib/a:second'; }\n"
for name, tail, part in [("identifier-escape", "exports.helper = helper;\nexports.h\\u0065lper = second;\n", "more than once"), ("string-subscript", "exports.helper = helper;\nexports['h\\x65lper'] = second;\n", "more than once"),
                         ("template-subscript", "exports.helper = helper;\nexports[`helper`] = second;\n", "more than once"), ("braced-escape", "exports.helper = helper;\nexports.h\\u{65}lper = second;\n", "more than once"),
                         ("object-literal-key", "module.exports = { helper, 'h\\x65lper': second };\n", "twice"), ("object-literal-identifier-key", "module.exports = { helper, h\\u0065lper: second };\n", "twice")]:
    probe(f"e3-duplicate-key-spelled-with-an-escape-{name}", {"lib/a.js": HELPER + SECOND + tail, M: MAIN_A + s(1, CALL_A)}, refuse={f"{M}:s1": part}, notes="the second write is the same name, and wins in node.")
probe("e3-key-spelled-with-an-escape-is-the-name", {"lib/a.js": HELPER + "exports.h\\u0065lper = helper;\n", M: MAIN_A + s(1, CALL_A)}, edge=[f"{M}:s1"], notes="exports.h\\u0065lper is exports.helper.")
# 4 and 5: a require of a file ripple does not read
TXT = {"lib/patch2.txt": "require('./a').helper = " + W + ";\n"}
def e4(name, code, part, notes=""):
    probe(f"e4-{name}", {"lib/a.js": LIB_A, **TXT, M: "const a = require('./lib/a');\n" + code + s(1, CALL_A)}, refuse={f"{M}:s1": part}, notes=notes or "the file that runs is a .txt, which ripple does not read.")
e4("computed-require-variable", "const where = './lib/patch2.txt';\nrequire(where);\n", "computed")
e4("computed-require-join", "require(['./lib', 'patch2.txt'].join('/'));\n", "computed")
e4("computed-require-escaped-literal", "require('./lib/patch2.t\\x78t');\n", "computed", "a literal with an escape is not read as one: it reflects.")
e4("optional-call-of-require", "require?.('./lib/patch2.txt');\n", "does not read")
e4("require-through-the-comma-operator", "(0, require)('./lib/patch2.txt');\n", "require")
e4("computed-require-in-a-function-never-called", "function never() { return require('./lib/' + 'patch2.txt'); }\n", "computed", "even a require that runs only if the function is called reflects: nothing says it does not run.")
for name, spec, files in [("file", "../node_modules/pkg/index.js", {"node_modules/pkg/index.js": "require('../../lib/a').helper = " + W + ";\n"}),
                          ("trailing-slash", "../node_modules/pkg/", {"node_modules/pkg/index.js": "require('../../lib/a').helper = " + W + ";\n"}),
                          ("scoped", "../node_modules/@s/pkg", {"node_modules/@s/pkg/index.js": "require('../../../lib/a').helper = " + W + ";\n"}),
                          ("deep-file", "../node_modules/pkg/lib/x", {"node_modules/pkg/lib/x.js": "require('../../../lib/a').helper = " + W + ";\n"}),
                          ("through-dot-dot", "./x/../../node_modules/pkg", {"node_modules/pkg/index.js": "require('../../lib/a').helper = " + W + ";\n"}),
                          ("package-main", "../node_modules/pkg", {"node_modules/pkg/package.json": "{\"main\": \"entry.js\"}\n", "node_modules/pkg/entry.js": "require('../../lib/a').helper = " + W + ";\n"}),
                          ("jspm-packages", "../jspm_packages/pkg", {"jspm_packages/pkg/index.js": "require('../../lib/a').helper = " + W + ";\n"}),
                          ("missing-below-node-modules", "../node_modules/nothing-here", {})]:
    p2(f"e5-relative-{name}", "require(" + repr(spec) + ");\n" if name != "missing-below-node-modules" else "try { require(" + repr(spec) + "); } catch (e) {}\n", part="does not read", extra=files,
       notes="a relative path into (or through) a directory the walk skips names a file node runs and ripple does not read." if name != "missing-below-node-modules" else "nothing is there, so node throws: ripple still refuses (what is there later is not known).", refuse=True)

# 6: the binding a call goes through is bound once, whatever binds it again
CONS = [("parameter-of-another-function", "const a = require('./lib/a');\nfunction other(a) { return a; }\nother(1);\n"), ("for-in-head", "var a = require('./lib/a');\nfor (var a in { x: 1 }) {}\n"),
        ("for-of-const-in-a-block", "const a = require('./lib/a');\nfor (const a of [1]) {}\n"), ("update-expression", "var a = require('./lib/a');\na++;\n"),
        ("object-destructuring-assignment", "var a = require('./lib/a');\n({ x: a } = { x: { helper() { return 'local'; } } });\n"), ("function-declaration-of-the-name", "var a = require('./lib/a');\nfunction a() {}\n"),
        ("nested-function-block", "var a = require('./lib/a');\nfunction f() { { function a() {} } }\n"), ("catch-destructured", "const a = require('./lib/a');\ntry { throw 1; } catch ({ a }) {}\n"),
        ("arrow-parameter", "const a = require('./lib/a');\nconst f = (a) => a;\nf(1);\n"), ("compound-assignment", "var a = require('./lib/a');\na ||= { helper() { return 'local'; } };\n"),
        ("switch-case-function", "var a = require('./lib/a');\nswitch (1) { case 1: function a() {} }\n"), ("try-block-function", "var a = require('./lib/a');\ntry { function a() {} } catch (e) {}\n")]
for name, code in CONS:
    probe(f"e6-binding-{name}", {"lib/a.js": LIB_A, M: code + s(1, CALL_A)}, refuse={f"{M}:s1": "more than on"}, notes="the name a call goes through is bound again in the file.")
probe("e6-package-name-bound-twice", {"node_modules/pkg/index.js": "exports.f = function f() { return 'node_modules/pkg:f'; };\n", M: "const pkg = require('pkg');\n{ function pkg() { return { f() { return 'local'; } }; } }\n" + s(1, "pkg.f()")},
      refuse={f"{M}:s1": "more than once"}, notes="a package name rebound by an Annex B function: not external.")
probe("e6-bound-once-stays-an-edge", {"lib/a.js": LIB_A, M: "const a = require('./lib/a');\nconst { helper: h } = require('./lib/a');\n" + s(1, CALL_A) + s(2, "h()")}, edge=[f"{M}:s1", f"{M}:s2"], notes="two names, each bound once.")


def main_():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, probe_ in PROBES.items():
        target = OUT / name
        if target.exists():
            shutil.rmtree(target)
        for rel, content in probe_["files"].items():
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for rel, destination in probe_["links"].items():  # a symbolic link (relative to its own directory), written as it is
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(destination)
        info = {k: probe_[k] for k in ("notes", "entries", "edge", "refuse")}
        (target / "PROBE.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
    stale = sorted(p.name for p in OUT.iterdir() if p.is_dir() and p.name not in PROBES)
    if stale:
        sys.exit(f"not in the table (delete them): {stale}")
    print(f"{len(PROBES)} probes in {OUT}")


if __name__ == "__main__":
    main_()
