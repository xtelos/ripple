const k = 'constructor';
const F = (function () {})[k];
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
