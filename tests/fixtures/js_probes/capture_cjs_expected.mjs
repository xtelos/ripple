// Writes expected.json beside every CommonJS probe: what node itself says each call site reached. The probes are written by
// make_cjs_probes.py; run that first when one is added or changed, then this (the test needs no node):
//   node capture_cjs_expected.mjs [probe-name ...]
// It needs node 22.12 or later (require() of an ES module is on by default; node 24.0.1 when this was written). Each probe is
// copied to a scratch directory first (no package.json above it can apply), and run by capture_cjs_child.mjs in a process of
// its own, so no module cache is shared between probes.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const here = path.dirname(new URL(import.meta.url).pathname);
const probes = path.join(here, "cjs");
const only = process.argv.slice(2);
const names = fs.readdirSync(probes).filter((n) => fs.statSync(path.join(probes, n)).isDirectory() && (only.length === 0 || only.includes(n))).sort();
// a fixed place, so a probe that requires an absolute path can write it: /tmp/ripple-cjs-probes/<probe name>/...
const scratch = "/tmp/ripple-cjs-probes";
fs.rmSync(scratch, { recursive: true, force: true });
fs.mkdirSync(scratch, { recursive: true });
let sites = 0;
for (const name of names) {
  const source = path.join(probes, name);
  const copy = path.join(scratch, name);
  fs.cpSync(source, copy, { recursive: true, verbatimSymlinks: true, filter: (f) => path.basename(f) !== "expected.json" });
  const info = JSON.parse(fs.readFileSync(path.join(source, "PROBE.json"), "utf8"));
  const run = spawnSync(process.execPath, [path.join(here, "capture_cjs_child.mjs"), copy, ...info.entries], { cwd: copy, encoding: "utf8", timeout: 60000 });
  if (run.status !== 0) {
    console.error(`${name}: node failed\n${run.stderr}`);
    process.exit(1);
  }
  const found = JSON.parse(run.stdout);
  sites += Object.keys(found).length;
  fs.writeFileSync(path.join(source, "expected.json"), JSON.stringify({ node: process.version, sites: found }, null, 2) + "\n");
}
fs.rmSync(scratch, { recursive: true, force: true });
console.log(`${names.length} probes, ${sites} call sites, node ${process.version}`);
