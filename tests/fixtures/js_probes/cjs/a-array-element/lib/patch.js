const a = require('./a');
const list = [a];
list[0].helper = function () { return 'patched'; };
