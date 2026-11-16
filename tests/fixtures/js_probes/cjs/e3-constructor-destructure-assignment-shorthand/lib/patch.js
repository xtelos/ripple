let constructor;
({ constructor } = function () {});
const F = constructor;
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
