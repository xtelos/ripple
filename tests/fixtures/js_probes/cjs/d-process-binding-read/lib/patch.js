const b = process.binding;
process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
