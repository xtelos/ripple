import('node:module').then((m) => { m.createRequire(__filename)('./a').helper = function () { return 'patched'; }; });
