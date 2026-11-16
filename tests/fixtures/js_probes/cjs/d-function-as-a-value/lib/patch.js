const F = Function;
const P = F('return process')();
P.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
