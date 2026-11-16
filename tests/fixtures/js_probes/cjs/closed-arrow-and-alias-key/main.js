const a = require('./lib/a');
exports.s1 = function () { return a.helper(); }; // site s1
exports.s2 = function () { return a.renamed(); }; // site s2
