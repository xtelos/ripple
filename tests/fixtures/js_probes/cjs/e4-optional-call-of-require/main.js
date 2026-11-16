const a = require('./lib/a');
require?.('./lib/patch2.txt');
exports.s1 = function () { return a.helper(); }; // site s1
