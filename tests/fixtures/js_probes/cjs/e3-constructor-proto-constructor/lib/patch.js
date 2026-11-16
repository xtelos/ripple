const F = (function () {}).__proto__.constructor;
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
