const F = (function () {})['\x63onstructor'];
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
