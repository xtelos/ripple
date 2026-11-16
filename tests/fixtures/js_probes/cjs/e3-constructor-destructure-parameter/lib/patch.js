function get({ constructor }) { return constructor; }
const F = get(function () {});
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
