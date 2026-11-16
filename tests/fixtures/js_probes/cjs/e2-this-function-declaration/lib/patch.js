function g() { return this; }
const G = g();
G.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
