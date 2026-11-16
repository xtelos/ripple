let a = require('./lib/a');
a = require('./lib/b');
exports.s1 = function () { return a.helper(); }; // site s1
