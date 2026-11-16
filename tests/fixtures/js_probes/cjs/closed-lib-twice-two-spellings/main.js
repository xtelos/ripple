const a = require('./lib/a');
const b = require('./lib/a.js');
exports.s1 = function () { return a.helper(); }; // site s1
exports.s2 = function () { return b.helper(); }; // site s2
