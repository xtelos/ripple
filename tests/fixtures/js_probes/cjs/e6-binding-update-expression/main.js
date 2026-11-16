var a = require('./lib/a');
a++;
exports.s1 = function () { return a.helper(); }; // site s1
