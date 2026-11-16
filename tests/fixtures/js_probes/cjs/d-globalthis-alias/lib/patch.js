const g = globalThis;
g.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
