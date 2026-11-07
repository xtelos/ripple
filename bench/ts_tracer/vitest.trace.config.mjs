// Wraps a project's vitest config with the tracer. Run from bench/ts_tracer/run.py, which sets:
//   RIPPLE_TS_ROOT      the project (a copy in the cache, so node_modules sits above the sources)
//   RIPPLE_TS_OUT_DIR   where each worker writes its edges
//   RIPPLE_VITEST_BASE  the project's own vitest.config.mts, merged in so aliases and environment match
// The plugin instruments the original TS/TSX (enforce 'pre', map null so the instrumented text is the
// source); Vite's esbuild then compiles it with a source map that vitest uses to map stack frames back.
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const here = path.dirname(new URL(import.meta.url).pathname);
const require = createRequire(import.meta.url);
const { instrument, ROOT } = require("./core.cjs");
const ts = createRequire(path.join(ROOT, "package.json"))("typescript");
const SRC = /\.tsx?$/;

const loaded = await import(pathToFileURL(process.env.RIPPLE_VITEST_BASE).href);
const base = typeof loaded.default === "function" ? await loaded.default({ command: "serve", mode: "test" }) : loaded.default;

export default {
  ...base,
  cacheDir: path.join(ROOT, ".vite"),  // vite would otherwise write into the shared, read-only node_modules
  plugins: [...(base.plugins || []), {
    name: "ripple-ts-trace",
    enforce: "pre",
    transform(code, id) {
      const file = id.split("?")[0];
      if (!file.startsWith(ROOT) || file.includes(`${path.sep}node_modules${path.sep}`) || !SRC.test(file)) return null;
      return { code: instrument(ts, file, code), map: null };
    },
  }],
  test: { ...base.test, root: ROOT, setupFiles: [path.join(here, "vitest-setup.mjs"), ...(base.test?.setupFiles || [])] },
};
