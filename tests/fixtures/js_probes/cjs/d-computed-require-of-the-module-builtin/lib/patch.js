const m = require(['mod', 'ule'].join(''));
m.createRequire(__filename)('./a').helper = function () { return 'patched'; };
