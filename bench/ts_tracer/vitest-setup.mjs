// Runs in each vitest worker before the test file: patch the project's own react, flush after each file.
import { createRequire } from "node:module";
import path from "node:path";
import { afterAll } from "vitest";

const core = createRequire(import.meta.url)("./core.cjs");
core.installReact(createRequire(path.join(core.ROOT, "package.json")));
afterAll(() => globalThis.__ripFlush());
