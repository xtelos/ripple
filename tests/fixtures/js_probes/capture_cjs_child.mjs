// Run by capture_cjs_expected.mjs, once per probe, in a copy of the probe: node capture_cjs_child.mjs DIR ENTRY...
// Loads each entry (a .mjs is imported, anything else is required), then calls every site (a line that ends in `// site sN`
// holds one call, inside the function the file exports as sN) and prints what each call reached as one JSON object:
//   {"main.js:s1": {"line": 3, "reached": "lib/a:helper"}}      the id the function that ran returned
//   {"main.js:s2": {"line": 4, "threw": "TypeError"}}            the call threw
//   {"main.js:s3": {"line": 5, "value": "string"}}               it returned something that is not an id (a string with no colon, a number, ...)
// A call that runs while its file loads (it cannot be a function the file exports) is written by the file as the object it
// exports as sN, `{ reached: id }` or `{ threw: name }`, and printed as it is.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [dir, ...entries] = process.argv.slice(2);
const require = createRequire(path.join(dir, "capture-entry.cjs"));
const loaded = [];
const failures = {};
for (const entry of entries) {
  const file = path.join(dir, entry);
  try {
    loaded.push([entry, entry.endsWith(".mjs") ? await import(pathToFileURL(file).href) : require(file)]);
  } catch (error) {
    loaded.push([entry, null]);
    failures[entry] = `${error && error.name}: ${error && error.message}`;
  }
}
const found = {};
for (const [entry, module] of loaded) {
  const lines = fs.readFileSync(path.join(dir, entry), "utf8").split("\n");
  lines.forEach((text, index) => {
    const match = text.match(/\/\/ site (s\d+)\s*$/);
    if (!match) return;
    const name = match[1];
    const key = `${entry}:${name}`;
    const fn = module && (module[name] ?? (module.default && module.default[name]));
    if (fn && typeof fn === "object") {
      // a call that runs while the file loads writes what it reached (or that it threw) as the object the file exports as sN
      found[key] = { line: index + 1, ...fn };
      return;
    }
    if (typeof fn !== "function") {
      found[key] = { line: index + 1, threw: `not exported (${failures[entry] || "no function " + name})` };
      return;
    }
    try {
      const result = fn();
      const label = typeof result === "string" && /^[\w./@-]+:[\w.#$-]+$/.test(result);
      found[key] = label ? { line: index + 1, reached: result } : { line: index + 1, value: typeof result === "string" ? "string" : typeof result };
    } catch (error) {
      found[key] = { line: index + 1, threw: String(error && error.name) };
    }
  });
}
process.stdout.write(JSON.stringify(found));
