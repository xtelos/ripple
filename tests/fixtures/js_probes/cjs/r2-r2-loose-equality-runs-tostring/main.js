const a = require('./lib/a');
if (a == 'x') {}
exports.s1 = function () { return a.helper(); }; // site s1
