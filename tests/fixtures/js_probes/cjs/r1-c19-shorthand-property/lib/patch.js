const a = require('./a');
const o = { a };
o.a.helper = function () { return 'patched'; };
