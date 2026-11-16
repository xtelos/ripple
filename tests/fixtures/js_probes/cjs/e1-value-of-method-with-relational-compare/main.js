const a = require('./lib/a');
if (a < 2) {}
exports.s1 = function () { return a.helper(); }; // site s1
