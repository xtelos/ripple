require('./lib/patch.txt');
const a = require('./lib/a');
exports.s1 = function () { return a.helper(); }; // site s1
