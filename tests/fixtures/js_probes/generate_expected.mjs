// Writes expected.json beside every probe: what the real tools make of each import site. The probes themselves are written by
// make_probes.py; run that first when a probe is added or changed, then this (the test needs no node):
//   node generate_expected.mjs /path/to/typescript [probe-name ...]
// where /path/to/typescript is a `typescript` package directory (5.9.3 when this was written). A probe is a directory with
// PROBE.json ({"notes", "imports": [[importer, specifier], ...], ...}) and the repo's files.
//
// It needs node 22.15 or later (module.registerHooks; node 24.0.1 when this was written) and a `typescript` package.
//
// Two judges answer for each site, and both are recorded:
//   the compiler: for each importer, a real tsserver says which project the file belongs to (`projectInfo`: a tsconfig.json
//     that includes it, the jsconfig.json beside one that does not, or an inferred project) and ts.resolveModuleName resolves the
//     specifier under that project's options (`resolved`). An inferred project has tsserver's defaults (allowJs, jsx preserve).
//     For a file that tsserver puts in a jsconfig.json or an inferred project while a tsconfig.json holds it too (`tsc -p`
//     compiles it with that one), `build_project` and `build` are the build's project and answer. `reach_dropped` lists, per
//     config that holds a same-named twin of the importer but not the importer itself (x.ts beside x.tsx), the file the
//     specifier reaches under that config's options: such a file is built like its twin, though it has no project.
//   node: for a JavaScript importer that node can run (.js, .mjs, .cjs), a child process runs a one-line module written beside
//     the importer (an `import` or a `require` of the specifier) under node, and a registerHooks resolve hook records which file
//     it resolved to (`node.file`) or the error code (`node.error`). Node is the runtime, so where it names a file the compiler
//     does not, the answer is not known, and where it fails (an extensionless import in ESM, say) it says nothing.
// Each probe is copied to a scratch directory first, so no config above the repo can apply, and a PROBE.json "links" map
// ({"node_modules/@me/util": "../../packages/util"}) is made as symlinks there (a package manager's workspace links).
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const [tsDir, ...only] = process.argv.slice(2);
if (!tsDir) {
  console.error("usage: node generate_expected.mjs /path/to/typescript [probe-name ...]");
  process.exit(2);
}
const ts = createRequire(import.meta.url)(path.resolve(tsDir));
const here = path.dirname(new URL(import.meta.url).pathname);
const probesDir = path.join(here, "probes");
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "js-probes-"));

class Server {
  constructor() {
    this.child = spawn(process.execPath, [path.join(path.resolve(tsDir), "lib", "tsserver.js"), "--disableAutomaticTypingAcquisition"], {
      stdio: ["pipe", "pipe", "inherit"],
    });
    this.buf = "";
    this.seq = 0;
    this.pending = new Map();
    this.child.stdout.on("data", (chunk) => {
      this.buf += chunk.toString();
      for (;;) {
        const m = this.buf.match(/Content-Length: (\d+)\r\n\r\n/);
        if (!m) return;
        const start = m.index + m[0].length;
        const length = Number(m[1]);
        if (Buffer.byteLength(this.buf.slice(start)) < length) return;
        const message = JSON.parse(this.buf.slice(start, start + length));
        this.buf = this.buf.slice(start + length);
        if (message.type === "response" && this.pending.has(message.request_seq)) {
          this.pending.get(message.request_seq)(message);
          this.pending.delete(message.request_seq);
        }
      }
    });
  }

  send(command, args) {
    this.seq += 1;
    const seq = this.seq;
    return new Promise((resolve) => {
      this.pending.set(seq, resolve);
      this.child.stdin.write(JSON.stringify({ seq, type: "request", command, arguments: args }) + "\n");
    });
  }

  fire(command, args) {
    this.seq += 1;
    this.child.stdin.write(JSON.stringify({ seq: this.seq, type: "request", command, arguments: args }) + "\n");
  }

  stop() {
    this.child.kill();
  }
}

// What tsserver gives a file no config holds (ProjectService's default options for an inferred project).
const INFERRED = { allowJs: true, jsx: ts.JsxEmit.Preserve, allowSyntheticDefaultImports: true, allowNonTsExtensions: true };

const host = { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => {} };
const parsedCache = new Map();
function parsedOf(configFile) {
  if (!parsedCache.has(configFile)) parsedCache.set(configFile, ts.getParsedCommandLineOfConfigFile(configFile, {}, host) ?? null);
  return parsedCache.get(configFile);
}
const optionsOf = (configFile) => parsedOf(configFile)?.options ?? {};

function copy(from, to) {
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    if (entry.name === "expected.json") continue;
    const source = path.join(from, entry.name);
    if (entry.isDirectory()) copy(source, path.join(to, entry.name));
    else fs.copyFileSync(source, path.join(to, entry.name));
  }
}

function link(root, links) {
  for (const [name, target] of Object.entries(links || {})) {
    fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    fs.symlinkSync(target, path.join(root, name));
  }
}

function configsUnder(root) {
  const found = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "node_modules") walk(file);
      } else if (/^(ts|js)config(\.[^/]*)?\.json$/.test(entry.name)) found.push(file);
    }
  };
  walk(root);
  return found.sort();
}

const holds = (parsed, file) => !!parsed && parsed.fileNames.some((name) => fs.existsSync(name) && fs.realpathSync(name) === file);

// the project `tsc -p` would build the file in: the nearest tsconfig.json up the tree that holds it, or a project it references does
function buildProject(root, file, seen = new Set()) {
  const projectOf = (configFile) => {
    if (seen.has(configFile)) return null;
    seen.add(configFile);
    const parsed = parsedOf(configFile);
    if (holds(parsed, file)) return configFile;
    for (const reference of parsed?.projectReferences ?? []) {
      let target = reference.path;
      if (fs.existsSync(target) && fs.statSync(target).isDirectory()) target = path.join(target, "tsconfig.json");
      if (fs.existsSync(target)) {
        const found = projectOf(target);
        if (found) return found;
      }
    }
    return null;
  };
  for (let dir = path.dirname(file); ; dir = path.dirname(dir)) {
    const candidate = path.join(dir, "tsconfig.json");
    if (fs.existsSync(candidate)) {
      const found = projectOf(candidate);
      if (found) return found;
    }
    if (dir === root || dir === path.dirname(dir)) return null;
  }
}

const NODE_HOOKS = path.join(scratch, "_hooks.mjs");
fs.writeFileSync(
  NODE_HOOKS,
  `import { registerHooks } from "node:module";
import fs from "node:fs";
let seen = false;
registerHooks({
  resolve(specifier, context, nextResolve) {
    const mine = !seen && specifier === process.env.SITE_SPEC;
    if (mine) seen = true;
    try {
      const result = nextResolve(specifier, context);
      if (mine) fs.appendFileSync(process.env.SITE_LOG, JSON.stringify({ url: result.url }) + "\\n");
      return result;
    } catch (error) {
      if (mine) fs.appendFileSync(process.env.SITE_LOG, JSON.stringify({ error: error.code || String(error) }) + "\\n");
      throw error;
    }
  },
});
`,
);

// what node does with the import of `specifier` from a module beside `importer`: the file it runs, or the error that stopped it
function runUnderNode(root, importer, specifier, tag) {
  const ext = path.extname(importer);
  const entry = path.join(root, path.dirname(importer), `__site_${tag}${ext}`);
  const log = path.join(scratch, `site_${tag}.log`);
  fs.writeFileSync(entry, ext === ".cjs" ? `require(${JSON.stringify(specifier)});\n` : `import * as m from ${JSON.stringify(specifier)};\n`);
  fs.writeFileSync(log, "");
  const run = spawnSync(process.execPath, ["--no-warnings", "--import", NODE_HOOKS, entry], {
    cwd: root,
    env: { ...process.env, SITE_SPEC: specifier, SITE_LOG: log },
    encoding: "utf8",
    timeout: 20000,
  });
  fs.rmSync(entry);
  const lines = fs.readFileSync(log, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const answer = lines[0] || {};
  if (answer.url && answer.url.startsWith("file:")) {
    const file = fs.realpathSync(new URL(answer.url).pathname);
    const relative = path.relative(root, file).split(path.sep).join("/");
    if (run.status === 0) return { file: relative, error: null };
    const code = (run.stderr.match(/ERR_[A-Z_]+/) || [run.stderr.includes("SyntaxError") ? "SyntaxError" : "failed"])[0];
    return { file: null, error: `${code} (resolved to ${relative})` };
  }
  if (answer.url) return { file: null, error: null, builtin: true };
  return { file: null, error: answer.error || "failed" };
}

const server = new Server();
const names = fs.readdirSync(probesDir).filter((n) => fs.statSync(path.join(probesDir, n)).isDirectory()).sort();
let count = 0;
for (const name of names) {
  if (only.length && !only.includes(name)) continue;
  const dir = path.join(probesDir, name);
  const probe = JSON.parse(fs.readFileSync(path.join(dir, "PROBE.json"), "utf8"));
  copy(dir, path.join(scratch, name));
  link(path.join(scratch, name), probe.links);
  const root = fs.realpathSync(path.join(scratch, name));
  const rel = (file) => path.relative(root, file).split(path.sep).join("/");
  const repoFile = (file) => (file && !file.includes("/node_modules/") ? rel(fs.realpathSync(file)) : null);
  const resolveWith = (specifier, file, options) => {
    const result = ts.resolveModuleName(specifier, file, options, ts.sys).resolvedModule;
    return result && !result.isExternalLibraryImport && !result.resolvedFileName.includes("/node_modules/") ? repoFile(result.resolvedFileName) : null;
  };
  const projects = new Map();
  const builds = new Map();
  const imports = [];
  const configs = configsUnder(root);
  let tag = 0;
  for (const [importer, specifier] of probe.imports) {
    const file = path.join(root, importer);
    const realFile = fs.realpathSync(file);
    if (!projects.has(importer)) {
      server.fire("open", { file });
      const info = await server.send("projectInfo", { file, needFileNameList: false });
      const configFile = info.body && info.body.configFileName;
      projects.set(importer, configFile && !configFile.startsWith("/dev/null") ? configFile : null);
      builds.set(importer, buildProject(root, realFile));
    }
    const configFile = projects.get(importer);
    const buildFile = builds.get(importer);
    const resolved = resolveWith(specifier, file, configFile ? optionsOf(configFile) : INFERRED);
    const built = buildFile && buildFile !== configFile ? resolveWith(specifier, file, optionsOf(buildFile)) : resolved;
    const reach = {};
    for (const config of configs) {
      const parsed = parsedOf(config);
      if (!parsed || holds(parsed, realFile)) continue;
      // the config holds a same-named twin of the importer (x.tsx beside x.ts) and would hold the importer too without it
      const stem = realFile.replace(/\.(d\.)?[cm]?[jt]sx?$/, "");
      const twins = parsed.fileNames.filter((name) => name !== realFile && name.replace(/\.(d\.)?[cm]?[jt]sx?$/, "") === stem);
      if (!twins.length) continue;
      const without = { ...host, readDirectory: (...args) => host.readDirectory(...args).filter((name) => !twins.includes(name)) };
      if (!holds(ts.getParsedCommandLineOfConfigFile(config, {}, without), realFile)) continue;
      const found = resolveWith(specifier, file, parsed.options);
      if (found) reach[rel(config)] = found;
    }
    const site = {
      importer,
      specifier,
      resolved,
      project: configFile ? rel(configFile) : "inferred",
      build_project: buildFile ? rel(buildFile) : null,
      build: buildFile ? built : null,
      reach_dropped: reach,
    };
    if (/\.(js|mjs|cjs)$/.test(importer)) {
      tag += 1;
      site.node = runUnderNode(fs.realpathSync(path.join(scratch, name)), importer, specifier, `${tag}`);
    }
    imports.push(site);
  }
  // where tsserver says each listed call's callee is defined: the first `name(` of the file that is not a declaration
  const definitions = [];
  for (const { in: file, call: callee } of probe.calls || []) {
    const absolute = path.join(root, file);
    server.fire("open", { file: absolute });
    const text = fs.readFileSync(absolute, "utf8");
    const match = [...text.matchAll(new RegExp(`(?<!function )\\b${callee}\\s*\\(`, "g"))][0];
    if (!match) throw new Error(`${name}: no call of ${callee} in ${file}`);
    const before = text.slice(0, match.index).split("\n");
    const reply = await server.send("definition", { file: absolute, line: before.length, offset: before[before.length - 1].length + 1 });
    const found = (reply.body || []).map((d) => rel(fs.realpathSync(d.file)));
    definitions.push({ in: file, call: callee, defined_in: found.length ? found[0] : null });
  }
  // the JSX settings of the project of each listed file
  const jsx = [];
  for (const file of probe.jsx || []) {
    const configFile = projects.get(file) ?? null;
    const options = configFile ? optionsOf(configFile) : INFERRED;
    jsx.push({ file, project: configFile ? rel(configFile) : "inferred", jsxFactory: options.jsxFactory ?? null, jsxFragmentFactory: options.jsxFragmentFactory ?? null, jsxImportSource: options.jsxImportSource ?? null });
  }
  fs.writeFileSync(path.join(dir, "expected.json"), JSON.stringify({ typescript: ts.version, node: process.version, imports, definitions, jsx }, null, 2) + "\n");
  count += imports.length;
  console.log(`${name}: ${imports.length} imports`);
}
server.stop();
fs.rmSync(scratch, { recursive: true, force: true });
console.log(`${count} imports`);
