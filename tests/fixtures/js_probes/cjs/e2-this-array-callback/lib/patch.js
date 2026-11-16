const G = [1].map(function () { return this; })[0];
G.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
