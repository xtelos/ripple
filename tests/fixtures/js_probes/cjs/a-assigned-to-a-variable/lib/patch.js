const a = require('./a');
let x;
x = a;
x.helper = function () { return 'patched'; };
