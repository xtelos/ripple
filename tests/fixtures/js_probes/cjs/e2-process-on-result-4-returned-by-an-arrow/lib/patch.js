const reg = () => process.once('x', () => {});
reg().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
