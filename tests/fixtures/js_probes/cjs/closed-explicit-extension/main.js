const a = require('./lib/a.js');
const c = require('./lib/c.cjs');
exports.s1 = function () { return a.helper(); }; // site s1
exports.s2 = function () { return c.viaC(); }; // site s2
