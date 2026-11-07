// Runs inside each Jest test sandbox (setupFilesAfterEnv): patch the sandbox's react, flush after the file.
const { createRequire } = require("module");
const path = require("path");
const core = require("./core.cjs");

core.installReact(createRequire(path.join(core.ROOT, "package.json")));
afterAll(() => globalThis.__ripFlush());
