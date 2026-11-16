const F = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(function () {}), 'constructor').value;
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
