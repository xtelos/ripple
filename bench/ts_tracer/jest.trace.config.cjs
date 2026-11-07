// Wraps a project's jest config with the tracer. Run from bench/ts_tracer/run.py, which sets
// RIPPLE_TS_ROOT, RIPPLE_TS_OUT_DIR and RIPPLE_JEST_CONFIG (the project's own jest.config.cjs).
const path = require("path");

const base = require(path.resolve(process.env.RIPPLE_JEST_CONFIG));
const tsRule = Object.entries(base.transform).find(([, v]) => Array.isArray(v) && v[0] === "ts-jest");
if (!tsRule) throw new Error("ripple tracer: the project's jest config has no ts-jest transform to chain to");

module.exports = {
  ...base,
  rootDir: path.resolve(process.env.RIPPLE_TS_ROOT),
  transform: { [tsRule[0]]: [path.join(__dirname, "jest-transform.cjs"), tsRule[1][1]] },
  setupFilesAfterEnv: [path.join(__dirname, "jest-setup.cjs"), ...(base.setupFilesAfterEnv || [])],
};
