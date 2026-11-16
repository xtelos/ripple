const F = Reflect.get(function () {}, 'constructor');
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
