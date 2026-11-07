// ripple TypeScript tracer: a source transform plus the runtime recorder it feeds.
//
// instrument() inserts a call to __rip(file, k) at the entry of every NAMED function in a TS/TSX file.
// At run time __rip reads the V8 stack, maps the first frame that belongs to an instrumented file back
// to TypeScript line and column, and names the function that contains that position. That function is
// the caller. Three kinds of edge come out:
//   call     caller -> callee for an ordinary call.
//   creator  the function whose body contains `<Child/>` -> Child. Recorded by patching React's
//            jsx / jsxs / jsxDEV / createElement and taking the function that was running.
//   renderer the nearest enclosing project component React was rendering -> the component React just
//            invoked. Read from React's current fiber and its `return` chain, because React calls a
//            child from its work loop and the parent is never on the stack.
//
// Names (the ids in bench/ground_truth_ts.json): `<module path without extension>:<qualified name>`.
//   * A module's id is its path from the project root, `index` collapsed to the directory.
//     Code that runs where a module is evaluated belongs to the module and is written without a colon.
//   * Functions, classes and methods are named by their declaration, nested as Outer.inner.
//   * A constructor is `Class.constructor`. A class with no constructor in source gets one inserted
//     (`constructor(...args){ super(...args) }`) and is named by the class alone, as Python names a
//     class with no __init__.
//   * An arrow or function expression that is the initializer of `const x = ...` is named x.
//   * The callback given to it() or test() is named by the titles of the describe() calls around it
//     and its own title, joined with " > ". A describe() callback is the module.
//   * Every other function is anonymous: it is not a callee, and code in it belongs to the named
//     function (or module) that contains it, as a Python lambda does.
"use strict";
const fs = require("fs");
const path = require("path");
const { fileURLToPath } = require("url");

const ROOT = path.resolve(process.env.RIPPLE_TS_ROOT || process.cwd()) + path.sep;
const OUT_DIR = process.env.RIPPLE_TS_OUT_DIR || path.join(ROOT, ".ripple-trace");

function moduleId(file) {
  const rel = path.relative(ROOT, file).split(path.sep).join("/").replace(/\.[cm]?[jt]sx?$/, "");
  return rel.replace(/(^|\/)index$/, "") || "index";
}

// ---------------------------------------------------------------- naming (compile time)

const unwrap = (ts, n) => {
  let c = n;
  while (c.parent && (ts.isParenthesizedExpression(c.parent) || ts.isAsExpression(c.parent) ||
                      ts.isSatisfiesExpression(c.parent) || ts.isNonNullExpression(c.parent))) c = c.parent;
  return c;
};

// describe/it/test with a literal title and a function as the last argument.
function testCallback(ts, fn, sf) {
  const call = fn.parent;
  if (!call || !ts.isCallExpression(call) || call.arguments[call.arguments.length - 1] !== fn) return null;
  if (!ts.isIdentifier(call.expression) || !/^(describe|it|test)$/.test(call.expression.text)) return null;
  const title = call.arguments[0];
  if (!title || !(ts.isStringLiteral(title) || ts.isNoSubstitutionTemplateLiteral(title))) return null;
  return { kind: call.expression.text === "describe" ? "describe" : "it", title: title.text };
}

function ownName(ts, n, sf) {
  if (ts.isFunctionDeclaration(n) || ts.isClassDeclaration(n)) return n.name ? n.name.text : null;
  if ((ts.isMethodDeclaration(n) || ts.isGetAccessor(n) || ts.isSetAccessor(n)) && ts.isClassLike(n.parent)) return n.name.getText(sf);
  if (ts.isConstructorDeclaration(n)) return "constructor";
  if (ts.isArrowFunction(n) || ts.isFunctionExpression(n)) {
    const test = testCallback(ts, n, sf);
    if (test && test.kind === "it") {
      const titles = [];
      for (let a = n.parent; a; a = a.parent) {
        const t = (ts.isArrowFunction(a) || ts.isFunctionExpression(a)) && testCallback(ts, a, sf);
        if (t && t.kind === "describe") titles.unshift(t.title);
      }
      return [...titles, test.title].join(" > ");
    }
    const top = unwrap(ts, n);
    const decl = top.parent;
    if (!test && decl && ts.isVariableDeclaration(decl) && decl.initializer === top && ts.isIdentifier(decl.name)) return decl.name.text;
  }
  return null;
}

function qualName(ts, node, sf) {
  const parts = [];
  for (let n = node; n && n !== sf; n = n.parent) {
    const nm = ownName(ts, n, sf);
    if (nm) parts.unshift(nm);
  }
  return moduleId(sf.fileName) + ":" + parts.join(".");
}

// ---------------------------------------------------------------- the source transform

function instrument(ts, file, source) {
  const kind = /\.tsx$/.test(file) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, kind);
  const key = JSON.stringify(file);
  const edits = [];  // [position, text, function index]
  const fns = [];    // [name, startPos, endPos] in original coordinates
  const register = (name, start, end) => fns.push([name, start, end]) - 1;
  const visit = (node) => {
    const fnLike = ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node) ||
      ts.isGetAccessor(node) || ts.isSetAccessor(node) || ts.isArrowFunction(node) || ts.isFunctionExpression(node);
    if (fnLike && node.body && ownName(ts, node, sf)) {
      // A class member's range starts at its parameter list. Its decorators are evaluated where the class is
      // defined, and the compilers map that call to the decorator or the member's name, so neither may be inside.
      const start = ts.isClassLike(node.parent) ? node.parameters.pos : node.getStart(sf);
      const k = register(qualName(ts, node, sf), start, node.getEnd());
      if (ts.isBlock(node.body)) edits.push([node.body.getStart(sf) + 1, `__rip(${key},${k});`, k]);
      else {
        edits.push([node.body.getStart(sf), `(__rip(${key},${k}),`, k]);
        edits.push([node.body.getEnd(), `)`, k]);
      }
    }
    if (ts.isClassDeclaration(node) && node.name && !node.members.some(ts.isConstructorDeclaration)) {
      const k = register(qualName(ts, node, sf), node.members.pos - 1, node.members.pos);
      const extend = (node.heritageClauses || []).some((h) => h.token === ts.SyntaxKind.ExtendsKeyword);
      edits.push([node.members.pos, extend ? `constructor(...__a: any[]){__rip(${key},${k});super(...__a);}` : `constructor(){__rip(${key},${k});}`, k]);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);

  edits.sort((a, b) => b[0] - a[0]);
  const inserts = {};  // line -> [column, length, function index], in original coordinates
  let out = source;
  for (const [pos, text, k] of edits) {
    out = out.slice(0, pos) + text + out.slice(pos);
    const lc = sf.getLineAndCharacterOfPosition(pos);
    (inserts[lc.line + 1] ||= []).push([lc.character + 1, text.length, k]);
  }
  for (const line of Object.keys(inserts)) inserts[line].sort((a, b) => a[0] - b[0]);
  const starts = [0];
  for (let i = 0; i < source.length; i++) if (source[i] === "\n") starts.push(i + 1);

  // The table is registered by a prefix on line 1. The prefix moves line 1's columns, so it records its own length.
  const me = [1, 0, -1];
  (inserts[1] ||= []).unshift(me);
  let prefix = "";
  for (let i = 0; i < 5; i++) {
    prefix = `__ripReg(${JSON.stringify({ file, fns, inserts, starts })});`;
    if (me[1] === prefix.length) break;
    me[1] = prefix.length;
  }
  if (me[1] !== prefix.length) throw new Error("ripple tracer: the line-1 prefix length did not settle");
  return prefix + out;
}

// ---------------------------------------------------------------- the recorder (run time)

const G = globalThis;
const S = G.__ripState ||= { tables: new Map(), edges: new Map(), pending: new Map(), fnName: new WeakMap(), diag: new Map(), src: new Map() };
const diag = (k) => S.diag.set(k, (S.diag.get(k) || 0) + 1);
G.__ripReg = (t) => { S.tables.set(t.file, t); };

// A frame's generated position -> the function that contains it, and the original line and column.
function locate(t, line, gcol) {
  let shift = 0;
  for (const [oc, len, k] of t.inserts[line] || []) {
    const at = oc + shift;
    if (gcol < at) break;
    if (gcol < at + len) return k >= 0 ? { fn: t.fns[k][0], line, col: oc } : null;  // inside text we inserted
    shift += len;
  }
  const col = gcol - shift;
  const pos = (t.starts[line - 1] ?? 0) + col - 1;
  let best = null;
  for (const f of t.fns) if (f[1] <= pos && pos < f[2] && (!best || f[1] >= best[1])) best = f;
  return { fn: best ? best[0] : moduleId(t.file), line, col };
}

const FRAME = /^\s*at (async )?(?:(.*?) \()?(.*?):(\d+):(\d+)\)?$/;
function frames(skip) {
  const limit = Error.stackTraceLimit;
  Error.stackTraceLimit = Infinity;
  try {
    const h = {};
    Error.captureStackTrace(h, skip);
    const out = [];
    for (const ln of String(h.stack).split("\n").slice(1)) {
      const m = ln.match(FRAME);
      if (m) out.push({ name: m[2] || "", file: m[3], line: +m[4], col: +m[5], async: !!m[1] });
    }
    return out;
  } finally { Error.stackTraceLimit = limit; }
}

// A helper the compiler emits into a file (esbuild's __decorateClass, tsc's __decorate) has no source
// position, so its frame must not be mapped: the caller is the project code that called the helper.
const COMPILER_HELPER = /(^|\.)__[A-Za-z]+$/;

function mapFrame(fr) {
  if (COMPILER_HELPER.test(fr.name)) return null;
  const f = fr.file.startsWith("file:") ? fileURLToPath(fr.file) : fr.file;
  const t = S.tables.get(f);
  const at = t && locate(t, fr.line, fr.col);
  return at ? { ...at, file: f, async: fr.async, site: `${path.relative(ROOT, f)}:${at.line}:${at.col}` } : null;
}

function bump(kind, from, to, site) {
  const key = JSON.stringify([kind, from, to, site]);
  S.edges.set(key, (S.edges.get(key) || 0) + 1);
}

// The text at a creator site must start with `<Tag`; a miss means a source map is off.
function tagAt(file, line, col) {
  if (!S.src.has(file)) S.src.set(file, fs.readFileSync(file, "utf8").split("\n"));
  const text = (S.src.get(file)[line - 1] || "").slice(col - 1);
  const m = text.match(/^<([A-Za-z_$][\w$.]*)/);
  return m ? m[1] : null;
}

// ---- React (function components; 19.x development builds)
let React = null;
const COMPONENT_CALLERS = /(^|\.)(renderWithHooks|renderWithHooksAgain|callComponentInDEV|mountIndeterminateComponent)$/;
const REACT_GLUE = /react_stack_bottom_frame|^bound |^Object\.apply$|^Reflect\.construct$/;
const IN_REACT_DOM = /[\\/]node_modules[\\/]react-dom[\\/]/;
const FUNCTION_FIBER_TAGS = new Set([0, 2, 11, 15]);

function currentFiber() {
  const internals = React && React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  return internals && internals.A && typeof internals.A.getOwner === "function" ? internals.A.getOwner() : null;
}

function rendererOf(fiber) {
  for (let p = fiber.return; p; p = p.return) {
    if (p.tag === 3) return "<root>";
    if (FUNCTION_FIBER_TAGS.has(p.tag) && S.fnName.has(p.type)) return S.fnName.get(p.type);
  }
  return "<detached>";
}

function installReact(req) {
  if (React) return;
  try { React = req("react"); } catch { diag("react-not-found"); return; }
  const wrap = (obj, name) => {
    if (!obj || typeof obj[name] !== "function" || obj[name].__rip) return;
    const orig = obj[name];
    const w = function () {
      const el = orig.apply(this, arguments);
      try { onCreate(arguments[0], w); } catch (e) { diag("onCreate-error:" + e.message); }
      return el;
    };
    w.__rip = true;
    obj[name] = w;
  };
  for (const id of ["react/jsx-runtime", "react/jsx-dev-runtime"]) {
    try { const rt = req(id); for (const n of ["jsx", "jsxs", "jsxDEV"]) wrap(rt, n); } catch { diag("no-" + id); }
  }
  wrap(React, "createElement");
  diag("react-version:" + React.version);
}

function onCreate(type, skip) {
  const fr = frames(skip);
  const direct = fr[0] && mapFrame(fr[0]);
  if (!direct) { diag("element-created-outside-instrumented-code"); return; }
  if (typeof type !== "function") { diag(typeof type === "string" ? "host-elements" : "non-function-element-type"); return; }
  if (!tagAt(direct.file, direct.line, direct.col)) diag("creator-site-not-on-tag");
  S.pending.set({ creator: direct.fn, site: direct.site, type }, 1);  // the child may not have rendered yet, so it is named at flush
}

G.__rip = function __rip(file, k) {
  const table = S.tables.get(file);
  if (!table) { diag("call-before-table-registered"); return; }  // vi.mock hoists above the line-1 prefix
  const callee = table.fns[k][0];
  const fr = frames(__rip);
  // fr[0] is the callee itself. A component React invoked has only React's glue between it and renderWithHooks.
  let byReact = false;
  for (let i = 1; i < Math.min(fr.length, 6); i++) {
    if (REACT_GLUE.test(fr[i].name)) continue;
    byReact = !mapFrame(fr[i]) && COMPONENT_CALLERS.test(fr[i].name);
    break;
  }
  if (byReact) {
    const fiber = currentFiber();
    if (!fiber || !FUNCTION_FIBER_TAGS.has(fiber.tag)) { diag("react-invoked-but-no-function-fiber"); return; }
    const parent = rendererOf(fiber);
    S.fnName.set(fiber.type, callee);
    bump("renderer", parent, callee, "-");
    return;
  }
  let caller = null, viaReactDom = false;
  for (let i = 1; i < fr.length && !caller; i++) {
    caller = mapFrame(fr[i]);
    if (!caller && IN_REACT_DOM.test(fr[i].file)) viaReactDom = true;
  }
  if (!caller) return;  // called by the runner or a library, not by project code
  if (viaReactDom) { diag("callback-invoked-by-react"); return; }
  bump("call", caller.fn, callee, caller.site + (caller.async ? " (async)" : ""));
};

G.__ripFlush = function flush() {
  for (const [rec] of S.pending) bump("creator", rec.creator, S.fnName.get(rec.type) || "?" + (rec.type.name || "anonymous"), rec.site);
  S.pending.clear();
  if (!S.edges.size && !S.diag.size) return;
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const name = path.join(OUT_DIR, `${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`);
  const edges = [...S.edges].map(([k, n]) => { const [kind, from, to, site] = JSON.parse(k); return { kind, from, to, site, count: n }; });
  fs.writeFileSync(name, JSON.stringify({ pid: process.pid, edges, diag: Object.fromEntries(S.diag) }));
  S.edges.clear(); S.diag.clear();
};
process.on("exit", G.__ripFlush);

module.exports = { instrument, ROOT, installReact };
