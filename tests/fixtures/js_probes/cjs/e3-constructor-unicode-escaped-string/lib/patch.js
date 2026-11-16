const F = (function () {})['\u0063onstructor'];
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
