function reg() { return process.on('x', () => {}); }
reg().getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
