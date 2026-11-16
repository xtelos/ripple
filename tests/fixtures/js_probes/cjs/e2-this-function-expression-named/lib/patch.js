const G = (function named() { return this; })();
G.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
