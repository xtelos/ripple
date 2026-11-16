const a = require('./a');
const x = process.argv.length > 0 ? a : {};
x.helper = function () { return 'patched'; };
