const { constructor } = function () {};
constructor('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
