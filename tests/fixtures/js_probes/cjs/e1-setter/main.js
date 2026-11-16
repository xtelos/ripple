const a = require('./lib/a');
a.boot = 1;
exports.s1 = function () { return a.helper(); }; // site s1
