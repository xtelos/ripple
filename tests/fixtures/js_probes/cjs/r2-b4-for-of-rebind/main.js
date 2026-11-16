var a = require('./lib/a');
for (a of [{ helper() { return 'local'; } }]) {}
exports.s1 = function () { return a.helper(); }; // site s1
