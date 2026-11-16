const g = (function () { return this; })();
g.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
