const { helper } = require('./lib/a');
const { helper: renamed } = require('./lib/a');
exports.s1 = function () { return helper(); }; // site s1
exports.s2 = function () { return renamed(); }; // site s2
