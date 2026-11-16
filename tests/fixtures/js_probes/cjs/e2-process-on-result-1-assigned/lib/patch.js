const p = process.on('x', () => {});
p.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
