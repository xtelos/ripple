"""Materialise the probe repos under tests/fixtures/js_probes/probes from the table below.

    python make_probes.py [OUT]        (OUT is the probes directory beside this file by default)

A probe is a name, the files of a small repo and the import sites to judge ([importer, specifier]). A file's content is
"" for a stub that exports `f`, None for an importer the maker writes (one `import * as mN from spec` and a call
`mN.f()` per site, which the test checks as an edge or no edge; a .cjs importer gets a `require` per site instead), a
dict or list for JSON, and any other string as it is. The maker deletes each probe directory it writes, expected.json
included: run generate_expected.mjs after it, so a probe is never edited by hand. PROBE.json keys besides imports:
  unresolved  {"importer|spec": why} sites where the compiler resolves a file but ripple must not (and "calls:file|name")
  calls       [{"in": file, "call": name}] calls checked against where tsserver says the callee is defined
  jsx         files whose project's JSX settings decide whether ripple claims React's order
  symbols     {query: symbol id} find_symbol answers written by hand
  links       {path: target} symlinks made in the generator's scratch copy (a workspace manager's node_modules links)
  walk        {"indexed": [...], "not_indexed": [...], "skipped": [...]} files the walk reads and directories it skips
  reasons     {"file|callee": part of the reason} an unresolved call that must say so
"""
import json
import shutil
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "probes"
ALL = ["a.ts", "b.tsx", "c.js", "d.jsx", "e.mjs", "f.cjs"]
JSI = ["c.js", "d.jsx", "e.mjs", "f.cjs"]
PROBES = {}


def probe(name, files=None, imports=None, notes="", unresolved=None, calls=None, jsx=None, symbols=None, links=None, walk=None, reasons=None):
    PROBES[name] = dict(files=files or {}, imports=imports or [], notes=notes, unresolved=unresolved or {}, calls=calls or [], jsx=jsx or [], symbols=symbols or {},
                        links=links or {}, walk=walk or {}, reasons=reasons or {})


def across(importers, specs):
    return [(i, s) for i in importers for s in specs]


def twins(importers, spec, why):
    return {f"{i}|{spec}": why for i in importers}


VITE_JS = "the compiler reads an extensionless import as x.ts, Node and Vite run x.js"
RUNTIME_JS = "the compiler reads ./x.js as x.ts, Node and the bundlers run x.js"


def cfg(**options):
    top = options.pop("_top", {})
    return json.dumps({"compilerOptions": options, **top}, indent=2)


T = {}  # shared one-liners
# ---- one file per name, every importer language -------------------------------------------------------------
probe("rel-ts-only", {"x.ts": ""}, across(ALL, ["./x", "./x.js", "./x.ts", "./x.mjs", "./x.tsx"]),
      "x.ts alone: extensionless, written .js (the build's name), written .ts (allowed only by newer settings) and a name nothing has.")
probe("rel-tsx-only", {"x.tsx": ""}, across(ALL, ["./x", "./x.tsx", "./x.jsx", "./x.js"]))
probe("rel-js-only", {"x.js": ""}, across(ALL, ["./x", "./x.js", "./x.jsx", "./x.mjs"]))
probe("rel-jsx-only", {"x.jsx": ""}, across(ALL, ["./x", "./x.jsx", "./x.js"]))
probe("rel-mjs-cjs-mts-cts", {"x.mjs": "", "y.cjs": "", "z.mts": "", "w.cts": ""},
      across(ALL, ["./x", "./x.mjs", "./y", "./y.cjs", "./z", "./z.mjs", "./z.mts", "./w", "./w.cjs", "./w.cts"]),
      "The module-format suffixes: an extensionless import never names one.")
# ---- twins ---------------------------------------------------------------------------------------------------
probe("twin-ts-js", {"x.ts": "", "x.js": ""}, across(ALL, ["./x", "./x.js", "./x.ts"]),
      "x.ts beside x.js. The compiler reads ./x and ./x.js as x.ts for every importer, and a TypeScript importer takes it (x.js is "
      "what the build makes of it). Node and the bundlers run x.js for a JavaScript importer, so those sites are unresolved.",
      unresolved={f"{i}|{s}": "compiler and runtime or bundler name different files" for i in ["c.js", "d.jsx", "e.mjs", "f.cjs"] for s in ["./x.js"]}
      | {f"{i}|./x": "Vite orders .js before .ts" for i in JSI})
probe("twin-ts-tsx-js-jsx-dts", {"x.ts": "", "x.tsx": "", "x.js": "", "x.jsx": "", "x.d.ts": ""}, across(ALL, ["./x", "./x.js", "./x.jsx", "./x.ts", "./x.tsx"]),
      unresolved={f"{i}|./x": "Vite orders .js before .ts" for i in JSI}
      | {f"{i}|./x.js": "compiler reads x.ts, runtime runs x.js" for i in ["c.js", "d.jsx", "e.mjs", "f.cjs"]}
      | {f"{i}|./x.jsx": "compiler reads x.tsx, runtime runs x.jsx" for i in ["c.js", "d.jsx", "e.mjs", "f.cjs"]})
probe("twin-tsx-jsx", {"x.tsx": "", "x.jsx": ""}, across(ALL, ["./x", "./x.jsx", "./x.js", "./x.tsx"]),
      unresolved={f"{i}|./x": "Vite orders .jsx before .tsx" for i in JSI} | {f"{i}|./x.jsx": "compiler reads x.tsx, runtime runs x.jsx" for i in ["c.js", "d.jsx", "e.mjs", "f.cjs"]})
probe("twin-js-jsx", {"x.js": "", "x.jsx": ""}, across(ALL, ["./x", "./x.js", "./x.jsx"]))
probe("twin-ts-mts", {"x.ts": "", "x.mts": ""}, across(ALL, ["./x", "./x.ts", "./x.mjs", "./x.mts"]),
      unresolved={f"{i}|./x": "Vite orders .mts before .ts" for i in ALL})
probe("dts-only", {"x.d.ts": "", "y.d.ts": "", "y.js": ""}, across(ALL, ["./x", "./x.js", "./x.d", "./x.d.ts", "./y", "./y.js"]),
      "A declaration file holds no code.")
probe("dts-beside-js", {"x.js": "", "x.d.ts": ""}, across(ALL, ["./x", "./x.js", "./x.d.ts"]),
      "The compiler names the declaration file for ./x; the code it describes is x.js.")
probe("dts-beside-ts", {"x.ts": "", "x.d.ts": ""}, across(ALL, ["./x", "./x.js", "./x.ts"]))
# ---- dual-format layouts -------------------------------------------------------------------------------------
dual_note = "The extensionless ./dual is dual.js for the compiler and for Node's require, and dual.mjs for Vite and Rollup (their resolve.extensions lists put .mjs first)."
probe("dual-js-mjs", {"dual.js": "", "dual.mjs": ""}, across(ALL, ["./dual", "./dual.js", "./dual.mjs"]), dual_note,
      unresolved={f"{i}|./dual": "Vite and Rollup read .mjs first" for i in ALL})
probe("dual-js-cjs", {"dual.js": "", "dual.cjs": ""}, across(ALL, ["./dual", "./dual.js", "./dual.cjs"]),
      "No mainstream resolver tries .cjs for an extensionless import, so every resolver reads ./dual as dual.js.")
probe("dual-js-mjs-cjs", {"dual.js": "", "dual.mjs": "", "dual.cjs": ""}, across(ALL, ["./dual", "./dual.js", "./dual.mjs", "./dual.cjs"]), dual_note,
      unresolved={f"{i}|./dual": "Vite and Rollup read .mjs first" for i in ALL})
probe("mjs-beside-dir-index", {"x.mjs": "", "x/index.js": ""}, across(ALL, ["./x", "./x/", "./x.mjs", "./x/index"]),
      "x.mjs and x/index.js: Vite takes x.mjs, the compiler and Node's require take the directory.",
      unresolved={f"{i}|./x": "Vite takes x.mjs, the compiler x/index.js" for i in ALL})
# ---- directories ---------------------------------------------------------------------------------------------
probe("dir-index-ts", {"lib/index.ts": "", "lib/sub.ts": ""}, across(ALL, ["./lib", "./lib/", "./lib/index", "./lib/index.js", "./lib/index.ts"]))
probe("dir-index-js", {"lib/index.js": ""}, across(ALL, ["./lib", "./lib/", "./lib/index", "./lib/index.js"]))
probe("dir-index-jsx-tsx", {"lib/index.jsx": "", "ui/index.tsx": ""}, across(ALL, ["./lib", "./lib/index", "./ui", "./ui/index.js", "./ui/index.jsx"]))
probe("dir-index-twins", {"lib/index.js": "", "lib/index.mjs": "", "ui/index.ts": "", "ui/index.mts": ""}, across(ALL, ["./lib", "./lib/", "./lib/index", "./lib/index.js", "./ui", "./ui/"]),
      "A directory with index.js and index.mjs: the compiler and Node's require take index.js, Vite and Rollup index.mjs (and index.mts before index.ts).",
      unresolved=twins(JSI, "./lib", "Vite and Rollup read lib/index.mjs first") | twins(JSI, "./lib/", "Vite and Rollup read lib/index.mjs first") | twins(JSI, "./lib/index", "Vite and Rollup read lib/index.mjs first")
      | twins(ALL, "./ui", "Vite reads ui/index.mts before ui/index.ts") | twins(ALL, "./ui/", "Vite reads ui/index.mts before ui/index.ts"))
probe("dir-index-mjs", {"lib/index.mjs": "", "cjs/index.cjs": ""}, across(ALL, ["./lib", "./lib/index.mjs", "./cjs", "./cjs/index.cjs"]),
      "An index.mjs or index.cjs is not found for a directory import.")
probe("dir-and-file", {"x.js": "", "x/index.js": "", "y.ts": "", "y/index.ts": "", "z.js": "", "z/index.ts": ""},
      across(ALL, ["./x", "./y", "./z", "./x/", "./y/", "./z/"]),
      "A file beats a directory of the same name, in each language; across languages the compiler and the bundlers order them differently.",
      unresolved=twins(JSI, "./z", "z.js and z/index.ts: the compiler reads the TypeScript directory first, Node and Vite the file"))
probe("dotdot-root-index-ts", {"index.ts": "", "test/b.ts": "", "test/c.js": "", "sub/deep/d.ts": "", "sub/deep/e.js": ""},
      [(i, s) for i in ["test/b.ts", "test/c.js"] for s in ["..", "../", "../index", "../index.js"]]
      + [(i, s) for i in ["sub/deep/d.ts", "sub/deep/e.js"] for s in ["../..", "../../", "../../index"]])
probe("dotdot-root-index-js", {"index.js": "", "test/b.ts": "", "test/c.js": "", "test/d.jsx": ""},
      [(i, s) for i in ["test/b.ts", "test/c.js", "test/d.jsx"] for s in ["..", "../", "../index", "../index.js"]])
probe("dotdot-siblings", {"src/a/x.ts": "", "src/b/y.js": "", "src/b/z.tsx": "", "src/c/importer.ts": "", "src/c/importer.js": ""},
      across(["src/c/importer.ts", "src/c/importer.js"], ["../a/x", "../a/x.js", "../b/y", "../b/y.js", "../b/z", "../b/z.jsx", "../a", "../.."]))
# ---- configs: which config governs which importer -----------------------------------------------------------
cfg_files = {"x.ts": "", "x.js": "", "paths/t.ts": "", "paths/j.js": ""}
probe("cfg-tsconfig-only", {**cfg_files, "tsconfig.json": cfg()}, across(ALL, ["./x", "./x.js", "@p/t", "@p/j"]),
      "A tsconfig without allowJs holds no .js file: a .js importer falls to an inferred project (and sees no paths).",
      unresolved=twins(JSI, "./x", VITE_JS) | twins(["c.js", "d.jsx", "e.mjs", "f.cjs"], "./x.js", RUNTIME_JS))
probe("cfg-tsconfig-paths", {**cfg_files, "tsconfig.json": cfg(baseUrl=".", paths={"@p/*": ["paths/*"]})}, across(ALL, ["@p/t", "@p/j", "@p/nothing"]))
probe("cfg-tsconfig-allowjs", {**cfg_files, "tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@p/*": ["paths/*"]})}, across(ALL, ["@p/t", "@p/j", "./x", "./x.js"]),
      unresolved=twins(JSI, "./x", VITE_JS) | twins(["c.js", "d.jsx", "e.mjs", "f.cjs"], "./x.js", RUNTIME_JS))
probe("cfg-tsconfig-checkjs", {**cfg_files, "tsconfig.json": cfg(checkJs=True, baseUrl=".", paths={"@p/*": ["paths/*"]})}, across(ALL, ["@p/t", "@p/j"]))
probe("cfg-tsconfig-allowjs-false", {**cfg_files, "tsconfig.json": cfg(allowJs=False, baseUrl=".", paths={"@p/*": ["paths/*"]})}, across(ALL, ["@p/t", "@p/j"]))
probe("cfg-jsconfig-only", {**cfg_files, "jsconfig.json": cfg(baseUrl=".", paths={"@p/*": ["paths/*"]})}, across(ALL, ["@p/t", "@p/j", "./x"]),
      "A jsconfig.json is read by tsserver for every file below it that no tsconfig.json holds, a TypeScript file included.",
      unresolved=twins(JSI, "./x", VITE_JS))
probe("cfg-both-tsconfig-holds-js", {**cfg_files, "tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@p/*": ["paths/*"]}),
                                     "jsconfig.json": cfg(baseUrl=".", paths={"@p/*": ["other/*"]}), "other/t.ts": "", "other/j.js": ""},
      across(ALL, ["@p/t", "@p/j"]))
probe("cfg-both-tsconfig-skips-js", {**cfg_files, "tsconfig.json": cfg(), "jsconfig.json": cfg(baseUrl=".", paths={"@p/*": ["paths/*"]})},
      across(ALL, ["@p/t", "@p/j"]),
      "The tsconfig.json holds no .js file (no allowJs), so the jsconfig.json beside it is the project of each one.")
probe("cfg-both-nested-jsconfig-beside-tsconfig",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@/*": ["src/*"]}), "src/util.js": "export function util() {}\n",
       "sub/tsconfig.json": json.dumps({"include": ["**/*.ts"]}), "sub/jsconfig.json": cfg(baseUrl=".", paths={"@/*": ["lib/*"]}),
       "sub/lib/util.js": "export function util() {}\n", "sub/app.js": "import { util } from '@/util';\nexport function app() { util(); }\n"},
      [("sub/app.js", "@/util")], "The reviewer's probe p4: sub/jsconfig.json, not the root tsconfig.json, is the project of sub/app.js.",
      calls=[{"in": "sub/app.js", "call": "util"}])
probe("cfg-jsconfig-jsx-factory",
      {"tsconfig.json": cfg(), "jsconfig.json": cfg(jsxFactory="h"),
       "panel.jsx": "import { Child } from './child';\nexport function Panel() { return <Child />; }\nexport function App() { return <Panel />; }\n",
       "child.jsx": "export function Child() { return null; }\n"},
      [("panel.jsx", "./child")], "The reviewer's probe p10: the jsconfig.json is panel.jsx's project, and it sets jsxFactory h.", jsx=["panel.jsx"])
probe("cfg-nested-tsconfig",
      {"tsconfig.json": cfg(baseUrl=".", paths={"@/*": ["src/*"]}), "src/a.ts": "", "pkg/tsconfig.json": cfg(baseUrl=".", paths={"@/*": ["lib/*"]}),
       "pkg/lib/a.ts": "", "pkg/main.ts": None, "main.ts": None},
      [("pkg/main.ts", "@/a"), ("main.ts", "@/a")])
probe("cfg-nested-tsconfig-js",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@/*": ["src/*"]}), "src/a.js": "", "pkg/tsconfig.json": cfg(baseUrl=".", paths={"@/*": ["lib/*"]}),
       "pkg/lib/a.js": "", "pkg/main.js": None, "pkg/main.ts": None, "main.js": None},
      [("pkg/main.js", "@/a"), ("pkg/main.ts", "@/a"), ("main.js", "@/a")],
      "pkg/tsconfig.json has no allowJs, so pkg/main.js belongs to the root config.")
probe("cfg-extends-relative",
      {"base.json": cfg(allowJs=True, baseUrl=".", paths={"@p/*": ["paths/*"]}), "tsconfig.json": json.dumps({"extends": "./base.json"}), **cfg_files},
      across(["a.ts", "c.js"], ["@p/t", "@p/j"]))
probe("cfg-extends-from-subdir",
      {"base.json": cfg(allowJs=True, baseUrl=".", paths={"@p/*": ["paths/*"]}), "app/tsconfig.json": json.dumps({"extends": "../base.json"}), "paths/t.ts": "", "paths/j.js": "",
       "app/a.ts": None, "app/a.js": None},
      across(["app/a.ts", "app/a.js"], ["@p/t", "@p/j"]), "paths are relative to the config that wrote them, not to the one that extends it.")
probe("cfg-references-solution",
      {"tsconfig.json": json.dumps({"files": [], "references": [{"path": "./app"}, {"path": "./lib"}]}),
       "app/tsconfig.json": json.dumps({"compilerOptions": {"composite": True, "baseUrl": ".", "paths": {"@l/*": ["../lib/src/*"]}}, "include": ["src"], "references": [{"path": "../lib"}]}),
       "lib/tsconfig.json": json.dumps({"compilerOptions": {"composite": True, "allowJs": True}, "include": ["src"]}),
       "app/src/a.ts": None, "lib/src/u.ts": "", "lib/src/v.js": "", "lib/src/w.js": None, "app/scripts/s.ts": None},
      [("app/src/a.ts", "@l/u"), ("app/src/a.ts", "@l/v"), ("lib/src/w.js", "./v"), ("lib/src/w.js", "./u"), ("app/scripts/s.ts", "@l/u")],
      "A solution-style root hands each file to the referenced project that holds it; app/scripts is held by no project.")
probe("cfg-include-src-only",
      {"tsconfig.json": json.dumps({"compilerOptions": {"allowJs": True, "baseUrl": ".", "paths": {"@p/*": ["paths/*"]}}, "include": ["src"]}),
       "src/a.ts": None, "src/a.js": None, "scripts/b.ts": None, "scripts/b.js": None, "paths/t.ts": "", "paths/j.js": ""},
      across(["src/a.ts", "src/a.js", "scripts/b.ts", "scripts/b.js"], ["@p/t", "@p/j"]),
      "scripts/ is outside include, so it has no project and no paths.")
probe("cfg-exclude-importer",
      {"tsconfig.json": json.dumps({"compilerOptions": {"allowJs": True, "baseUrl": ".", "paths": {"@p/*": ["paths/*"]}}, "exclude": ["legacy"]}),
       "src/a.ts": None, "legacy/b.ts": None, "legacy/b.js": None, "paths/t.ts": "", "paths/j.js": ""},
      across(["src/a.ts", "legacy/b.ts", "legacy/b.js"], ["@p/t", "@p/j"]))
probe("cfg-files-list",
      {"tsconfig.json": json.dumps({"compilerOptions": {"allowJs": True, "baseUrl": ".", "paths": {"@p/*": ["paths/*"]}}, "files": ["src/a.ts", "src/c.js"]}),
       "src/a.ts": None, "src/b.ts": None, "src/c.js": None, "src/d.js": None, "paths/t.ts": "", "paths/j.js": ""},
      across(["src/a.ts", "src/b.ts", "src/c.js", "src/d.js"], ["@p/t", "@p/j"]))
probe("cfg-outdir-excludes-importer",
      {"tsconfig.json": json.dumps({"compilerOptions": {"allowJs": True, "outDir": "out", "baseUrl": ".", "paths": {"@p/*": ["paths/*"]}}}),
       "src/a.ts": None, "out/a.js": None, "paths/t.ts": "", "paths/j.js": ""},
      across(["src/a.ts", "out/a.js"], ["@p/t", "@p/j"]), "A file under outDir is left out of the program by default.")
_stems = ["m.ts", "m.js", "n.tsx", "n.js", "o.d.ts", "o.js", "p.js", "p.jsx", "q.ts", "q.tsx", "r.mts", "r.mjs", "s.cts", "s.cjs", "t.ts", "t.mjs", "u.ts", "u.jsx", "v.tsx", "v.jsx"]
probe("cfg-same-stem-priority",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@p/*": ["paths/*"]}), "paths/t.ts": "", **{f: ("" if f.endswith(".d.ts") else None) for f in _stems}},
      [(f, "@p/t") for f in _stems if not f.endswith(".d.ts")],
      "A wildcard include drops a file when a same-named file of a higher extension priority is in it too (m.js beside m.ts): the dropped one is in no project.")
probe("cfg-same-stem-files-list",
      {"tsconfig.json": json.dumps({"compilerOptions": {"allowJs": True, "baseUrl": ".", "paths": {"@p/*": ["paths/*"]}}, "files": ["m.ts", "m.js"]}), "paths/t.ts": "", "m.ts": None, "m.js": None},
      [("m.ts", "@p/t"), ("m.js", "@p/t")], "files names a file outright, so the priority rule does not drop it.")
# ---- baseUrl and paths ---------------------------------------------------------------------------------------
probe("baseurl-root", {"tsconfig.json": cfg(allowJs=True, baseUrl="."), "src/x.ts": "", "src/y.js": "", "src/z.tsx": "", "lib/w.js": ""},
      across(ALL, ["src/x", "src/y", "src/z", "lib/w", "src/y.js"]))
probe("baseurl-root-no-allowjs", {"tsconfig.json": cfg(baseUrl="."), "src/x.ts": "", "src/y.js": ""}, across(ALL, ["src/x", "src/y"]))
probe("baseurl-src", {"tsconfig.json": cfg(allowJs=True, baseUrl="src"), "src/x.ts": "", "src/y.js": "", "src/lib/index.js": ""}, across(["src/a.ts", "src/a.js", "src/a.jsx"], ["x", "y", "lib", "nothing"]))
probe("baseurl-dotdot", {"app/tsconfig.json": cfg(allowJs=True, baseUrl=".."), "shared/s.ts": "", "shared/t.js": "", "app/a.ts": None, "app/a.js": None},
      across(["app/a.ts", "app/a.js"], ["shared/s", "shared/t"]))
probe("paths-single", {"tsconfig.json": cfg(baseUrl=".", paths={"@t/*": ["src/*"]}), "src/f.ts": "", "src/g.js": "", "src/h.tsx": ""}, across(ALL, ["@t/f", "@t/g", "@t/h"]))
probe("paths-single-allowjs", {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@t/*": ["src/*"]}), "src/f.ts": "", "src/g.js": "", "src/h.tsx": "", "src/i.jsx": ""},
      across(ALL, ["@t/f", "@t/g", "@t/h", "@t/i"]))
probe("paths-multi-js-first",
      {"tsconfig.json": cfg(baseUrl=".", paths={"@lib/*": ["legacy/*", "src/*"]}), "legacy/foo.js": "export function foo() {}\n", "src/foo.ts": "export function foo() {}\n", "app/main.ts": "import { foo } from '@lib/foo';\nexport function run() { foo(); }\n", "app/main.js": None},
      across(["app/main.ts", "app/main.js"], ["@lib/foo"]),
      "The reviewer's probe p1: no allowJs, legacy/foo.js is no candidate, and the compiler reaches src/foo.ts.", calls=[{"in": "app/main.ts", "call": "foo"}])
probe("paths-multi-js-first-allowjs",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@lib/*": ["legacy/*", "src/*"]}), "legacy/foo.js": "", "src/foo.ts": "", "app/main.ts": None, "app/main.js": None},
      across(["app/main.ts", "app/main.js"], ["@lib/foo"]))
probe("paths-multi-ts-first",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@lib/*": ["src/*", "legacy/*"]}), "legacy/foo.js": "", "src/foo.ts": "", "legacy/bar.js": "", "app/main.ts": None, "app/main.js": None},
      across(["app/main.ts", "app/main.js"], ["@lib/foo", "@lib/bar"]))
probe("paths-multi-ts-then-ts",
      {"tsconfig.json": cfg(baseUrl=".", paths={"@lib/*": ["a/*", "b/*"]}), "a/x.ts": "", "b/x.ts": "", "b/y.ts": "", "main.ts": None},
      across(["main.ts"], ["@lib/x", "@lib/y", "@lib/z"]))
probe("paths-exact-and-wildcard",
      {"tsconfig.json": cfg(baseUrl=".", paths={"app": ["src/app.ts"], "app/*": ["src/app/*"], "@u/*": ["a/*", "b/*"]}), "src/app.ts": "", "src/app/x.ts": "", "src/app/x.js": "", "a/q.js": "", "b/q.ts": "", "main.ts": None},
      across(["main.ts"], ["app", "app/x", "@u/q"]))
probe("paths-fallback-baseurl",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@m/*": ["miss/*"]}), "src/f.ts": "", "src/g.js": "", "@m/src/f.ts": "", "main.ts": None, "main.js": None},
      across(["main.ts", "main.js"], ["@m/f", "src/f", "src/g"]))
probe("paths-star-with-suffix",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"*.fx": ["fx/*"], "@s/*": ["src/*.js", "src/*.ts"]}), "fx/a.ts": "", "fx/b.js": "", "src/c.js": "", "src/d.ts": "", "main.ts": None},
      across(["main.ts"], ["a.fx", "b.fx", "@s/c", "@s/d", "@s/c.js"]))
probe("paths-no-baseurl", {"tsconfig.json": cfg(paths={"@t/*": ["./src/*"]}), "src/f.ts": "", "main.ts": None}, across(["main.ts"], ["@t/f"]))
probe("rootdirs", {"tsconfig.json": cfg(rootDirs=["src", "generated"]), "src/a.ts": None, "generated/x.ts": "", "src/y.ts": ""}, across(["src/a.ts"], ["./x", "./y"]),
      "rootDirs make ./x from src/a.ts name generated/x.ts; ripple does not follow rootDirs and must not call it missing.",
      unresolved=twins(["src/a.ts"], "./x", "rootDirs: the file is in another root, which ripple does not follow"))
# ---- compiled output next to sources -------------------------------------------------------------------------
probe("output-outdir",
      {"packages/core/tsconfig.json": cfg(outDir="out", declaration=True), "packages/core/src/math.ts": "export function clamp() {}\n",
       "packages/core/src/use.ts": None, "packages/core/out/math.js": "export function clamp() {}\n", "packages/core/out/math.d.ts": "export declare function clamp(): void;\n"},
      [("packages/core/src/use.ts", "./math"), ("packages/core/src/use.ts", "./math.js")],
      "The reviewer's probe p7: the emitted out/math.js (the reviewer built into dist, which .gitignore keeps out of a commit) is compiler output, which the walk skips (and lists), so find_symbol stays unique.",
      symbols={"clamp": "packages/core/src/math:clamp", "math:clamp": "packages/core/src/math:clamp"})
probe("output-in-place", {"x.ts": "", "x.js": "", "x.d.ts": "", "y.ts": "", "y.js": "", "y.js.map": "", "use.ts": None}, across(["use.ts"], ["./x", "./x.js", "./y", "./y.js"]),
      "In-place emit: x.js and y.js sit beside the x.ts and y.ts they were built from.")
# ---- re-exports, graph level ---------------------------------------------------------------------------------
fn = "export function {n}() {{}}\n"
probe("star-two-ts-stars",
      {"barrel.ts": "export * from './a';\nexport * from './b';\n", "a.ts": fn.format(n="foo") + fn.format(n="only_a"), "b.ts": fn.format(n="foo") + fn.format(n="only_b"),
       "user.ts": "import { foo, only_a, only_b } from './barrel';\nexport function run() { foo(); only_a(); only_b(); }\n"},
      [("user.ts", "./barrel")], "Two stars both providing foo: the compiler reports TS2308 and the name is exported by neither.",
      calls=[{"in": "user.ts", "call": "foo"}, {"in": "user.ts", "call": "only_a"}, {"in": "user.ts", "call": "only_b"}], unresolved={"calls:user.ts|foo": "TS2308: ambiguous star exports"})
probe("star-ts-star-js",
      {"barrel.ts": "export * from './legacy.js';\nexport * from './modern';\n", "legacy.js": fn.format(n="foo") + fn.format(n="old"), "modern.ts": fn.format(n="foo") + fn.format(n="new_"),
       "user.ts": "import { foo, old, new_ } from './barrel';\nexport function run() { foo(); old(); new_(); }\n"},
      [("user.ts", "./barrel"), ("barrel.ts", "./legacy.js")],
      "The reviewer's probe p9, TypeScript side: the second star is a JavaScript file ripple does not read into this graph, so the barrel's export set is unknown.",
      calls=[{"in": "user.ts", "call": "foo"}, {"in": "user.ts", "call": "old"}, {"in": "user.ts", "call": "new_"}],
      unresolved={"calls:user.ts|foo": "TS2308: ambiguous star exports", "calls:user.ts|new_": "legacy.js may export new_ too: its exports are not read into this graph"})
probe("star-js-star-ts",
      {"barrel.js": "export * from './t';\nexport * from './j';\n", "t.ts": fn.format(n="bar") + fn.format(n="from_t"), "j.js": fn.format(n="bar") + fn.format(n="from_j"),
       "user.js": "import { bar, from_t, from_j } from './barrel';\nexport function go() { bar(); from_t(); from_j(); }\n"},
      [("user.js", "./barrel"), ("barrel.js", "./t")], "The reviewer's probe p9, JavaScript side.",
      calls=[{"in": "user.js", "call": "bar"}, {"in": "user.js", "call": "from_t"}, {"in": "user.js", "call": "from_j"}],
      unresolved={"calls:user.js|bar": "ambiguous star exports", "calls:user.js|from_j": "t.ts may export from_j too: its exports are not read into this graph"})
probe("star-single-cross-language",
      {"barrel.js": "export * from './a';\n", "a.ts": fn.format(n="ta"), "user.js": "import { ta } from './barrel';\nexport function go() { ta(); }\n"},
      [("user.js", "./barrel"), ("barrel.js", "./a")], "A lone star onto a file of the other language: the name is not 'not exported', it is not known.", calls=[{"in": "user.js", "call": "ta"}])
probe("star-explicit-beats-star",
      {"barrel.ts": "export * from './legacy.js';\nexport function foo() {}\n", "legacy.js": fn.format(n="foo"), "user.ts": "import { foo } from './barrel';\nexport function run() { foo(); }\n"},
      [("user.ts", "./barrel")], "A name the barrel declares itself is its own, whatever a star provides.", calls=[{"in": "user.ts", "call": "foo"}])
probe("star-same-language-single",
      {"barrel.ts": "export * from './a';\n", "a.ts": fn.format(n="foo"), "user.ts": "import { foo } from './barrel';\nexport function run() { foo(); }\n",
       "u.js": "import { g } from './b2';\nexport function go() { g(); }\n", "b2.js": "export * from './c2';\n", "c2.js": fn.format(n="g")},
      [("user.ts", "./barrel")], calls=[{"in": "user.ts", "call": "foo"}, {"in": "u.js", "call": "g"}])
probe("star-barrel-of-barrels",
      {"top.ts": "export * from './mid';\n", "mid.ts": "export * from './leaf';\nexport * from './other.js';\n", "leaf.ts": fn.format(n="deep"), "other.js": fn.format(n="far"),
       "user.ts": "import { deep, far } from './top';\nexport function run() { deep(); far(); }\n"},
      [("user.ts", "./top")], "deep is behind two stars but the mid barrel also stars a JavaScript file that could define it.",
      calls=[{"in": "user.ts", "call": "deep"}, {"in": "user.ts", "call": "far"}],
      unresolved={"calls:user.ts|deep": "other.js may export deep too: its exports are not read into this graph"})
probe("reexport-forms-ts",
      {"barrel.ts": "export * as ns from './a';\nexport { b as renamed } from './b';\nexport { default } from './d';\nexport { default as dd } from './d';\n",
       "a.ts": fn.format(n="fa"), "b.ts": fn.format(n="b"), "d.ts": "export default function dflt() {}\n",
       "user.ts": "import dflt, { ns, renamed, dd } from './barrel';\nexport function run() { ns.fa(); renamed(); dflt(); dd(); }\n"},
      [("user.ts", "./barrel")], calls=[{"in": "user.ts", "call": "fa"}, {"in": "user.ts", "call": "renamed"}, {"in": "user.ts", "call": "dflt"}, {"in": "user.ts", "call": "dd"}])
probe("reexport-forms-cross",
      {"barrel.ts": "export * as ns from './a.js';\nexport { b as renamed } from './b.js';\nexport { default } from './d.js';\n",
       "c.js": fn.format(n="fa"), "b.js": fn.format(n="b"), "d.js": "export default function dflt() {}\n",
       "user.ts": "import dflt, { ns, renamed } from './barrel';\nexport function run() { ns.fa(); renamed(); dflt(); }\n"},
      [("user.ts", "./barrel"), ("barrel.ts", "./a.js"), ("barrel.ts", "./b.js"), ("barrel.ts", "./d.js")],
      calls=[{"in": "user.ts", "call": "fa"}, {"in": "user.ts", "call": "renamed"}, {"in": "user.ts", "call": "dflt"}])
probe("reexport-forms-js",
      {"barrel.js": "export * as ns from './a.js';\nexport { b as renamed } from './b.js';\nexport { default } from './d.js';\nexport { default as dd } from './d.js';\n",
       "c.js": fn.format(n="fa"), "b.js": fn.format(n="b"), "d.js": "export default function dflt() {}\n",
       "user.js": "import dflt, { ns, renamed, dd } from './barrel';\nexport function run() { ns.fa(); renamed(); dflt(); dd(); }\n"},
      [("user.js", "./barrel")], calls=[{"in": "user.js", "call": "fa"}, {"in": "user.js", "call": "renamed"}, {"in": "user.js", "call": "dflt"}, {"in": "user.js", "call": "dd"}])
probe("reexport-js-from-ts",
      {"barrel.js": "export { t as renamed } from './t';\nexport * as tns from './t';\n", "t.ts": fn.format(n="t"),
       "user.js": "import { renamed, tns } from './barrel';\nexport function go() { renamed(); tns.t(); }\n"},
      [("user.js", "./barrel"), ("barrel.js", "./t")], calls=[{"in": "user.js", "call": "renamed"}, {"in": "user.js", "call": "t"}])

# ==== round 2: what a package.json, a directory or a config that does not hold a file decides ===========================
STUB = "export function f() {}\n"
PKG_WHY = "the directory's package.json (main, module, types, exports) picks the file, and ripple does not read it"


def pj(**fields):
    return fields


def every(importers, specs, why):
    return {f"{i}|{s}": why for i in importers for s in specs}


def under(prefix, importers):
    return [f"{prefix}{i}" for i in importers]


# ---- a directory that holds a package.json: its `main`, `module`, `types` or `exports` picks the file
probe("dir-pkg-main-other", {"pk/package.json": pj(main="./main.js"), "pk/main.js": "", "pk/index.js": ""}, across(ALL, ["./pk", "./pk/", "./pk/main.js"]),
      "The reviewer's round-2 probe zz-pkg-main: pk has index.js and a package.json whose main is main.js, which tsserver and Node run.",
      unresolved=every(ALL, ["./pk", "./pk/"], PKG_WHY))
probe("dir-pkg-main-no-index", {"pk/package.json": pj(main="lib/entry.js"), "pk/lib/entry.js": ""}, across(ALL, ["./pk", "./pk/", "./pk/lib/entry"]),
      "No index file at all: the package.json alone names the file.", unresolved=every(ALL, ["./pk", "./pk/"], PKG_WHY))
probe("dir-pkg-types-only", {"pk/package.json": pj(types="./src/index.d.ts"), "pk/src/index.d.ts": "", "pk/index.js": ""}, across(ALL, ["./pk", "./pk/"]),
      "`types` sends the compiler to a declaration file while Node's require runs index.js.", unresolved=every(ALL, ["./pk", "./pk/"], PKG_WHY))
probe("dir-pkg-exports-only", {"pk/package.json": pj(exports="./x.js"), "pk/x.js": "", "pk/index.js": ""}, across(ALL, ["./pk", "./pk/"]),
      "`exports` does not apply to a relative path for Node or the compiler (index.js), but a bundler's directory resolution reads it.",
      unresolved=every(ALL, ["./pk", "./pk/"], PKG_WHY))
probe("dir-pkg-module-field", {"pk/package.json": pj(main="./index.js", module="./esm.js"), "pk/index.js": "", "pk/esm.js": ""}, across(ALL, ["./pk", "./pk/"]),
      "`module` is what a bundler reads and `main` what Node and the compiler read.", unresolved=every(ALL, ["./pk", "./pk/"], PKG_WHY))
probe("dir-pkg-type-module", {"pk/package.json": pj(type="module"), "pk/index.js": "", "ek/package.json": pj(private=True), "ek/index.ts": ""}, across(ALL, ["./pk", "./pk/index.js", "./ek", "./ek/"]),
      "A package.json with no entry field and an index file: every tool reads the index (Node's ESM refuses a directory), and ripple still does not read the package.json, "
      "so the import is left unresolved; the file named in full is a plain edge.", unresolved=every(ALL, ["./pk", "./ek", "./ek/"], PKG_WHY))
probe("dir-no-index-no-package", {"lib/a.ts": "", "lib/b.js": "", "assets/logo.svg": "<svg/>\n"}, across(ALL, ["./lib", "./lib/", "./assets", "./assets/logo.svg"]),
      "A directory of the repo that no tool resolves is no package and no missing path: it is unresolved, with the reason.")
probe("dir-beside-file-pkg", {"x.js": "", "x/package.json": pj(main="m.js"), "x/m.js": ""}, across(ALL, ["./x", "./x/"]),
      "x.js beside a directory x with a package.json: the compiler's TypeScript pass enters the directory first.", unresolved=every(ALL, ["./x", "./x/"], PKG_WHY))
probe("dir-root-package",
      {"package.json": pj(main="./lib/main.js"), "index.js": "", "lib/main.js": "", **{f"test/{i}": None for i in ["a.ts", "c.js", "e.mjs", "f.cjs"]}},
      [(f"test/{i}", s) for i in ["a.ts", "c.js", "e.mjs", "f.cjs"] for s in ["..", "../", "../index.js", "../lib/main"]],
      "The root has a package.json and an index.js: `..` is the file main names.",
      unresolved=every([f"test/{i}" for i in ["a.ts", "c.js", "e.mjs", "f.cjs"]], ["..", "../"], PKG_WHY))
probe("dir-alias-paths",
      {"jsconfig.json": cfg(paths={"lib/*": ["./*"]}), "hooks/package.json": pj(types="src/index.d.ts"), "hooks/src/index.d.ts": "", "hooks/src/index.js": "",
       "plain/package.json": pj(main="src/index.js"), "plain/src/index.js": "", "bare/x.js": ""},
      [(i, s) for i in under("compat/src/", ALL) for s in ["lib/hooks", "lib/plain", "lib/bare", "lib/hooks/src/index.js", "../../plain"]],
      "The preact layout (the reviewer's zz-preact-sub): a path alias onto directories whose package.json picks the entry; 265 sites in preact's own JavaScript.",
      unresolved=every(under("compat/src/", ALL), ["lib/hooks", "lib/plain", "../../plain"], PKG_WHY))
probe("dir-alias-baseurl",
      {"tsconfig.json": cfg(allowJs=True, baseUrl="."), "pkg/package.json": pj(main="x.js"), "pkg/x.js": "", "pkg/index.js": "", "empty/y.ts": ""},
      across(ALL, ["pkg", "pkg/x", "empty"]), "A directory reached through baseUrl.", unresolved=every(ALL, ["pkg"], PKG_WHY))
probe("dir-alias-earlier-dir",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@x/*": ["a/*", "b/*"]}), "a/foo/package.json": pj(main="bar.js"), "a/foo/bar.js": "", "b/foo.ts": ""},
      across(ALL, ["@x/foo"]), "The first path target is a directory with a package.json: the compiler tries it before b/foo.ts.", unresolved=every(ALL, ["@x/foo"], PKG_WHY))

# ---- #imports, a package's name for itself, workspace packages
HASH = "a `#` import is mapped by the nearest package.json's `imports`, which ripple does not read"
probe("imports-subpath",
      {"package.json": pj(name="mypkg", type="module", imports={"#lib/*": "./lib/*"}), "lib/helper.js": ""},
      across(ALL, ["#lib/helper.js", "#lib/helper", "#nothing"]),
      "Node resolves #lib/helper.js to lib/helper.js; the compiler (no node16 or bundler resolution here) resolves nothing.")
probe("imports-subpath-bundler",
      {"package.json": pj(name="mypkg", type="module", imports={"#lib/*": "./lib/*"}), "lib/helper.js": "", "tsconfig.json": cfg(allowJs=True, module="esnext", moduleResolution="bundler")},
      across(ALL, ["#lib/helper.js", "#nothing"]), "With bundler resolution the compiler reads `imports` too.", unresolved=every(ALL, ["#lib/helper.js"], HASH))
probe("self-reference",
      {"package.json": pj(name="mypkg", type="module", exports={".": "./lib/main.js", "./sub": "./lib/sub.js"}), "lib/main.js": "", "lib/sub.js": ""},
      across(ALL, ["mypkg", "mypkg/sub", "mypkg-other"]), "A package imports itself by its name, through `exports`: Node runs lib/main.js; mypkg-other is another package.")
probe("self-reference-bundler",
      {"package.json": pj(name="mypkg", type="module", exports={".": "./lib/main.js", "./sub": "./lib/sub.js"}), "lib/main.js": "", "lib/sub.js": "",
       "tsconfig.json": cfg(allowJs=True, module="esnext", moduleResolution="bundler")},
      across(ALL, ["mypkg", "mypkg/sub"]), unresolved=every(ALL, ["mypkg", "mypkg/sub"], "the package's own name is resolved through its `exports`, which ripple does not read"))
probe("workspace-names",
      {"package.json": pj(private=True, workspaces=["packages/*"]), "packages/util/package.json": pj(name="@me/util", main="./src/index.js", type="module"),
       "packages/util/src/index.js": "", "packages/plain/package.json": pj(name="plainpkg", main="./index.js", type="module"), "packages/plain/index.js": ""},
      across(ALL, ["@me/util", "@me/util/src/index.js", "plainpkg", "@me/other", "react"]),
      "Workspace packages are linked into node_modules by the package manager (the generator makes the links): Node runs the repo's own files. @me/other and react are not in the repo.",
      links={"node_modules/@me/util": "../../packages/util", "node_modules/plainpkg": "../packages/plain"},
      unresolved=every(ALL, ["@me/util", "@me/util/src/index.js", "plainpkg"], "a workspace package is reached through its package.json name, which ripple does not follow"))
probe("bare-controls", {"package.json": pj(name="controls", private=True)}, across(ALL, ["react", "lodash/fp", "@scope/pkg", "node:fs", "fs", "@types/node"]),
      "None of these is a package of this repo.")
probe("package-name-nested",
      {"packages/a-lib/package.json": pj(name="a-lib", main="./src/index.ts"), "packages/a-lib/src/index.ts": "", **{i: None for i in ["a.ts", "c.js"]}},
      across(["a.ts", "c.js"], ["a-lib", "a-lib/src/index", "b-lib"]), "A package.json anywhere in the repo that names a package makes its name the repo's.")

# ---- which config holds a file, for TypeScript and JavaScript importers
STRAY_WHY = "tsserver opens the file in the nearer jsconfig.json and resolves nothing; the build's tsconfig.json maps the alias"
probe("cfg-ts-stray-jsconfig",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@/*": ["src/*"]}, _top={"include": ["src"]}), "src/legacy/jsconfig.json": cfg(checkJs=True),
       "src/util.ts": "", **{f"src/legacy/{i}": None for i in ["a.ts", "b.tsx", "c.js", "e.mjs"]}},
      across(under("src/legacy/", ["a.ts", "b.tsx", "c.js", "e.mjs"]), ["@/util", "../util"]),
      "The reviewer's round-2 probe zz-stray-jsconfig: a jsconfig.json nearer than the tsconfig.json that includes a TypeScript file. tsc builds the file with the "
      "tsconfig.json's paths (the edge main drew), where tsserver opens it in the jsconfig.json; a JavaScript file is the jsconfig.json's, as tsserver says.")
probe("cfg-ts-beyond-jsconfig",
      {"tsconfig.json": cfg(baseUrl=".", paths={"@/*": ["src/*"]}, _top={"include": ["src", "app"]}), "app/jsconfig.json": cfg(baseUrl=".", paths={"@/*": ["app/lib/*"]}),
       "src/util.ts": "", "app/lib/util.ts": "", "app/main.ts": None},
      [("app/main.ts", "@/util")], "tsserver puts app/main.ts in app/jsconfig.json (app/lib/util.ts); tsc builds it in the root tsconfig.json (src/util.ts): the build's answer is the one drawn.")
probe("cfg-ts-jsconfig-when-no-tsconfig-holds",
      {"jsconfig.json": cfg(baseUrl=".", paths={"@/*": ["src/*"]}, _top={"include": ["src"]}), "src/util.ts": "", "scripts/b.ts": None, "scripts/d.js": None,
       "src/a.ts": None, "src/c.js": None},
      across(["src/a.ts", "src/c.js", "scripts/b.ts", "scripts/d.js"], ["@/util"]),
      "No tsconfig.json: the jsconfig.json is the project of the files it includes, a TypeScript file too; scripts/ is outside its include.")
probe("cfg-tsconfig-build-json",
      {"tsconfig.json": {"files": [], "references": [{"path": "./tsconfig.build.json"}]},
       "tsconfig.build.json": cfg(allowJs=True, baseUrl=".", paths={"@/*": ["src/*"]}, _top={"include": ["src"]}), "src/util.ts": "",
       **{f"src/{i}": None for i in ["a.ts", "b.tsx", "c.js", "e.mjs"]}, "other/x.ts": None},
      [*across(under("src/", ["a.ts", "b.tsx", "c.js", "e.mjs"]), ["@/util"]), ("other/x.ts", "@/util")],
      "The usual library layout: tsconfig.json is `files: []` plus a reference to tsconfig.build.json, which holds the paths and the include.")
probe("cfg-extends-base-json",
      {"tsconfig.base.json": cfg(allowJs=True, baseUrl=".", paths={"@/*": ["src/*"]}), "tsconfig.json": {"extends": "./tsconfig.base.json", "include": ["src"]},
       "src/util.ts": "", **{f"src/{i}": None for i in ["a.ts", "c.js"]}, "scripts/s.ts": None, "scripts/t.js": None},
      across(["src/a.ts", "src/c.js", "scripts/s.ts", "scripts/t.js"], ["@/util"]), "paths from a base config, an include in the extending one; scripts/ is outside it.")
probe("cfg-configdir-paths",
      {"tsconfig.base.json": cfg(allowJs=True, baseUrl="${configDir}", paths={"@/*": ["${configDir}/src/*"]}),
       "packages/a/tsconfig.json": {"extends": "../../tsconfig.base.json", "include": ["src"]}, "packages/b/tsconfig.json": {"extends": "../../tsconfig.base.json", "include": ["src"]},
       "packages/a/src/util.ts": "", "packages/b/src/util.ts": "", **{f"packages/{p}/src/{i}": None for p in "ab" for i in ["a.ts", "c.js"]}},
      [(f"packages/{p}/src/{i}", s) for p in "ab" for i in ["a.ts", "c.js"] for s in ["@/util", "src/util"]],
      "TypeScript 5.5's ${configDir}: in a shared base config it is the directory of the config that extends it, so each package maps to its own src.")
probe("cfg-twin-alias",
      {"tsconfig.json": cfg(allowJs=True, baseUrl=".", paths={"@/*": ["src/*"]}, _top={"include": ["src"]}), "src/lib.ts": "export function lib() {}\n",
       "src/x.ts": "import { lib } from '@/lib';\nexport function xt() {\n  lib();\n}\n", "src/x.tsx": "import { lib } from '@/lib';\nexport function xr() {\n  lib();\n}\n",
       "src/p.js": None, "src/p.jsx": None, "src/q.ts": None, "src/q.js": None, "src/r.mts": None, "src/r.mjs": None},
      [("src/x.tsx", "@/lib"), ("src/x.ts", "@/lib"), ("src/p.js", "@/lib"), ("src/p.jsx", "@/lib"), ("src/q.ts", "@/lib"), ("src/q.js", "@/lib"), ("src/r.mts", "@/lib"), ("src/r.mjs", "@/lib")],
      "The reviewer's round-2 probes e-tsx, e-jsx and zz-tsx-twin: a file that a wildcard include drops for a same-named twin (x.tsx beside x.ts) is in no project, "
      "yet it is built with the alias, so its alias is neither a package nor an edge.",
      reasons={"src/x.tsx|lib": "which project builds it is not known"})
probe("cfg-twin-alias-references",
      {"tsconfig.json": {"files": [], "references": [{"path": "./app"}]},
       "app/tsconfig.json": {"compilerOptions": {"composite": True, "allowJs": True, "baseUrl": ".", "paths": {"@/*": ["src/*"]}}, "include": ["src"]},
       "app/src/lib.ts": "", "app/src/m.ts": None, "app/src/m.tsx": None, "app/src/n.js": None, "app/src/n.jsx": None},
      [("app/src/m.ts", "@/lib"), ("app/src/m.tsx", "@/lib"), ("app/src/n.js", "@/lib"), ("app/src/n.jsx", "@/lib")], "The same twins in a project a solution-style root references.")
probe("cfg-nested-no-include-reach",
      {"tsconfig.json": cfg(baseUrl=".", paths={"@/*": ["src/*"]}, _top={"include": ["src"]}), "src/lib/tsconfig.json": {"include": ["only"]}, "src/util.ts": "",
       "src/lib/a.ts": None, "src/lib/only/b.ts": None},
      [("src/lib/a.ts", "@/util"), ("src/lib/only/b.ts", "@/util")], "A nested config that holds only src/lib/only gives its file no alias; the root holds src/lib/a.ts.")

# ---- compiled output and where the walk looks
probe("outdir-configdir",
      {"tsconfig.base.json": cfg(outDir="${configDir}/dist", declaration=True), "packages/core/tsconfig.json": {"extends": "../../tsconfig.base.json", "include": ["src"]},
       "packages/core/src/math.ts": "export function clamp() {}\n", "packages/core/src/use.ts": "import { clamp } from './math';\nexport function u() {\n  clamp();\n}\n",
       "packages/core/dist/math.js": "export function clamp() {}\n", "packages/core/dist/use.js": "import { clamp } from './math';\nexport function u() {\n  clamp();\n}\n",
       "packages/core/dist/math.d.ts": "export declare function clamp(): void;\n"},
      [("packages/core/src/use.ts", "./math")], "The reviewer's round-2 probe f-configdir: ${configDir} in a shared base's outDir is the extending config's directory.",
      symbols={"clamp": "packages/core/src/math:clamp", "math:clamp": "packages/core/src/math:clamp", "u": "packages/core/src/use:u"},
      walk={"indexed": ["packages/core/src/math.ts"], "not_indexed": ["packages/core/dist/math.js", "packages/core/dist/use.js"], "skipped": ["packages/core/dist"]})
probe("outdir-build-json",
      {"packages/core/tsconfig.json": {"files": [], "references": [{"path": "./tsconfig.build.json"}]}, "packages/core/tsconfig.build.json": cfg(outDir="./dist", composite=True, _top={"include": ["src"]}),
       "packages/core/src/math.ts": "export function clamp() {}\n", "packages/core/dist/math.js": "export function clamp() {}\n"},
      [], "The reviewer's round-2 probe f-buildcfg: the outDir is in the config the library's tsconfig.json references.",
      symbols={"clamp": "packages/core/src/math:clamp"}, walk={"not_indexed": ["packages/core/dist/math.js"], "skipped": ["packages/core/dist"]})
probe("outdir-extends-configdir-references",
      {"tsconfig.base.json": cfg(outDir="${configDir}/lib"), "packages/core/tsconfig.json": {"files": [], "references": [{"path": "./tsconfig.build.json"}]},
       "packages/core/tsconfig.build.json": {"extends": "../../tsconfig.base.json", "include": ["src"]},
       "packages/core/src/math.ts": "export function clamp() {}\n", "packages/core/lib/math.js": "export function clamp() {}\n"},
      [], "Both mechanisms: a reference to a config that extends a base with ${configDir}.", symbols={"clamp": "packages/core/src/math:clamp"},
      walk={"not_indexed": ["packages/core/lib/math.js"], "skipped": ["packages/core/lib"]})
probe("outdir-is-another-sources-dir",
      {"src/app.ts": "import { core } from './core';\nexport function app() {\n  core();\n}\n", "src/core.ts": "export function core() {}\n",
       "test/fixtures/proj/tsconfig.json": cfg(outDir="../../../src"), "test/fixtures/proj/f.ts": "export function f() {}\n"},
      [("src/app.ts", "./core")], "The reviewer's round-2 probe f-outdir-src: a fixture's tsconfig.json writes its output into the repo's real src, which holds TypeScript source.",
      calls=[{"in": "src/app.ts", "call": "core"}], walk={"indexed": ["src/app.ts", "src/core.ts"], "skipped": []})
probe("outdir-over-python",
      {"app/core.py": "def core():\n    pass\n", "app/run.py": "from app.core import core\n\n\ndef run():\n    core()\n", "web/tsconfig.json": cfg(outDir="../app"), "web/w.ts": "export function w() {}\n"},
      [], "The reviewer's round-2 probe f-outdir-py: the outDir of a TypeScript config is a Python package, which the TypeScript walk skips and the Python walk reads.",
      walk={"indexed": ["app/core.py", "app/run.py", "web/w.ts"]})
probe("outdir-holds-typescript-source",
      {"packages/a/tsconfig.json": {"extends": "../../tsconfig.base.json"}, "tsconfig.base.json": cfg(outDir="dist"), "packages/a/src/x.ts": "import { y } from './dist/y';\nexport function x() {\n  y();\n}\n",
       "packages/a/src/dist/y.ts": "export function y() {}\n", "packages/b/tsconfig.json": cfg(outDir="dist"),
       "packages/b/src/z.ts": "import { w } from '../dist/w';\nexport function z() {\n  w();\n}\n", "packages/b/dist/w.ts": "export function w() {}\n"},
      [("packages/a/src/x.ts", "./dist/y"), ("packages/b/src/z.ts", "../dist/w")], "The reviewer's round-2 probe f-outdir-mono: a TypeScript source in an outDir is not output.",
      calls=[{"in": "packages/a/src/x.ts", "call": "y"}, {"in": "packages/b/src/z.ts", "call": "w"}],
      walk={"indexed": ["packages/a/src/dist/y.ts", "packages/b/dist/w.ts"], "skipped": []})
probe("outdir-in-place",
      {"tsconfig.json": cfg(outDir="./", rootDir="./ts"), "ts/a.ts": "import { b } from './b';\nexport function a() {\n  b();\n}\n", "ts/b.ts": "export function b() {}\n"},
      [("ts/a.ts", "./b")], "outDir ./ is in-place emit.", walk={"indexed": ["ts/a.ts", "ts/b.ts"], "skipped": []})
probe("outdir-equals-rootdir",
      {"tsconfig.json": cfg(outDir="src", rootDir="src"), "src/a.ts": "import { b } from './b';\nexport function a() {\n  b();\n}\n", "src/b.ts": "export function b() {}\n"},
      [("src/a.ts", "./b")], "An outDir that is the rootDir holds the sources.", walk={"indexed": ["src/a.ts", "src/b.ts"], "skipped": []})
probe("outdir-jsconfig",
      {"jsconfig.json": cfg(outDir="out"), "src/a.js": "export function a() {}\n", "out/a.js": "export function a() {}\n"},
      [], "A jsconfig.json names an outDir as a tsconfig.json does.", symbols={"a": "src/a:a"}, walk={"not_indexed": ["out/a.js"], "skipped": ["out"]})

# ---- re-exports
probe("star-barrel-of-barrels-2",
      {"top.ts": "export * from './mid';\nexport * from './alt';\n", "mid.ts": "export * from './other.js';\n", "alt.ts": fn.format(n="far2"), "other.js": fn.format(n="far2"),
       "user.ts": "import { far2 } from './top';\nexport function run() {\n  far2();\n}\n"},
      [("user.ts", "./top")], "The reviewer's round-2 probe star-bb2: far2 comes from the second star and from a JavaScript file behind the first; tsc reports TS2308.",
      calls=[{"in": "user.ts", "call": "far2"}], unresolved={"calls:user.ts|far2": "ambiguous across a star and a star onto an unread file"})
probe("star-onto-declaration-or-missing",
      {"b1.ts": "export * from './types';\nexport * from './real';\n", "types.d.ts": "export declare function foo(): void;\n", "real.ts": fn.format(n="foo"),
       "b3.ts": "export * from './missing';\nexport * from './real';\n",
       "use1.ts": "import { foo } from './b1';\nexport function u1() {\n  foo();\n}\n", "use3.ts": "import { foo } from './b3';\nexport function u3() {\n  foo();\n}\n"},
      [("use1.ts", "./b1"), ("use3.ts", "./b3"), ("b1.ts", "./types"), ("b3.ts", "./missing")],
      "The reviewer's round-2 probe i-stars: a star onto a declaration file or a missing file may provide foo too (tsc: TS2308 and TS2307), so no edge to real.ts.",
      calls=[{"in": "use1.ts", "call": "foo"}, {"in": "use3.ts", "call": "foo"}],
      unresolved={"calls:use1.ts|foo": "types.d.ts may provide foo too", "calls:use3.ts|foo": "./missing may provide foo too"})
probe("import-of-commonjs",
      {"c.js": "function f() {}\nfunction g() {}\nmodule.exports = { f: g, g: f };\n", "m.mjs": "import { f, g } from './c.js';\nexport function run() {\n  f();\n  g();\n}\n"},
      [("m.mjs", "./c.js")], "The reviewer's round-2 probe js-cjs: Node's cjs-module-lexer gives an ES import of a CommonJS file its names, ripple does not model them.",
      calls=[{"in": "m.mjs", "call": "f"}, {"in": "m.mjs", "call": "g"}], unresolved={"calls:m.mjs|f": "CommonJS", "calls:m.mjs|g": "CommonJS"},
      reasons={"m.mjs|f": "is CommonJS", "m.mjs|g": "is CommonJS"})


# ==== the writer ====================================================================================================
def generated(rel, specs):
    if rel.endswith(".cjs"):
        return "".join(f"require({json.dumps(s)});\n" for s in specs)
    return "".join(f"import * as m{n} from {json.dumps(s)};\nexport function call{n}() {{ m{n}.f(); }}\n" for n, s in enumerate(specs))


for name, spec in PROBES.items():
    root = OUT / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    importers = {}
    for importer, specifier in spec["imports"]:
        importers.setdefault(importer, []).append(specifier)
    files = dict(spec["files"])
    for importer, specs in importers.items():
        files.setdefault(importer, None)
    made = []
    for rel, content in files.items():
        if content is None:
            content = generated(rel, importers.get(rel, []))
            if not rel.endswith(".cjs"):
                made.append(rel)
        elif isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2) + "\n"
        elif content == "" and not rel.endswith(".json"):
            content = "export declare function f(): void;\n" if rel.endswith(".d.ts") else STUB
        else:
            content = content.replace("(); ", "();\n  ")  # one call to a line, so a call is found by its line
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    meta = {"notes": spec["notes"], "imports": [list(i) for i in spec["imports"]], "generated": made}
    for key in ("unresolved", "calls", "jsx", "symbols", "links", "walk", "reasons"):
        if spec[key]:
            meta[key] = spec[key]
    (root / "PROBE.json").write_text(json.dumps(meta, indent=2) + "\n")
print(len(PROBES), "probes", sum(len(s["imports"]) for s in PROBES.values()), "imports")
