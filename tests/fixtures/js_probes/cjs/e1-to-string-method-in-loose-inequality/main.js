const a = require('./lib/a');
if (a != 'y') {}
exports.s1 = function () { return a.helper(); }; // site s1
