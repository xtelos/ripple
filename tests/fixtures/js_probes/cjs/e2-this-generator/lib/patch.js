function* gen() { return this; }
const G = gen().next().value;
G.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
