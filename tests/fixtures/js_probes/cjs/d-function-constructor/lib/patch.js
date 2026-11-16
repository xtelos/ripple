const P = Function('return process')();
P.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
