const p = process;
p.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
