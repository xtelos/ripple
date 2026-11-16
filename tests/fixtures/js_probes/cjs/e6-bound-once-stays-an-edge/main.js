const a = require('./lib/a');
const { helper: h } = require('./lib/a');
exports.s1 = function () { return a.helper(); }; // site s1
exports.s2 = function () { return h(); }; // site s2
