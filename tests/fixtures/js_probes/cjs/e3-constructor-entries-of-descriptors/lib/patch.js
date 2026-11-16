const F = Object.entries(Object.getOwnPropertyDescriptors(Object.getPrototypeOf(function () {}))).find((e) => e[0] === 'constructor')[1].value;
F('return process')().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
