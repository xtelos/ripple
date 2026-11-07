// Jest transformer: instrument the original TS/TSX, then hand it to the project's own ts-jest, whose
// inline source map Jest uses to map stack frames. The project's ts-jest options arrive as this
// transformer's own config (see jest.trace.config.cjs).
"use strict";
const path = require("path");
const { createRequire } = require("module");
const { instrument, ROOT } = require("./core.cjs");

const req = createRequire(path.join(ROOT, "package.json"));
const ts = req("typescript");
let tsJest = req("ts-jest");
if (tsJest.default) tsJest = tsJest.default;

module.exports = {
  createTransformer(options) {
    const inner = tsJest.createTransformer(options);
    return {
      canInstrument: inner.canInstrument,
      getCacheKey: (...args) => inner.getCacheKey(...args),
      process(src, filename, ...rest) {
        const mine = filename.startsWith(ROOT) && !filename.includes(`${path.sep}node_modules${path.sep}`);
        const out = inner.process(mine ? instrument(ts, filename, src) : src, filename, ...rest);
        // ts-jest has been seen to compile from the file on disk instead of the text it was given.
        if (mine && !String(out.code).includes("__ripReg")) throw new Error(`ripple tracer: ts-jest dropped the instrumentation of ${filename}`);
        return out;
      },
    };
  },
};
