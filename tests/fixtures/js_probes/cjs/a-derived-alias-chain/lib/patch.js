const a = require('./a');
const b = a;
const c = b;
c.helper = function () { return 'patched'; };
