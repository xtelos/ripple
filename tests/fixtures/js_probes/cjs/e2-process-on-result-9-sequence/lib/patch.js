(0, process.on('x', () => {})).getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
