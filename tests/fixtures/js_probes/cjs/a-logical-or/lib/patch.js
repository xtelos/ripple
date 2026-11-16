const a = require('./a');
const x = a || {};
x.helper = function () { return 'patched'; };
