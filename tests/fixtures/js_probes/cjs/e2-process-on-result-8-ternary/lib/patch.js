const p = true ? process.on('x', () => {}) : null;
p.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
