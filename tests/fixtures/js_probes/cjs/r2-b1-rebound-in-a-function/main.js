let a = require('./lib/a');
function swap() { a = { helper() { return 'local'; } }; }
swap();
exports.s1 = function () { return a.helper(); }; // site s1
