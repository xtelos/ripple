const m = { f() { return this; } }.f;
const G = m();
G.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
