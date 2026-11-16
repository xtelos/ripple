const use = (p) => p.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
use(process.once('x', () => {}));
