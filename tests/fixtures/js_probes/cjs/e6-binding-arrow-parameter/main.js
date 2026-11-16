const a = require('./lib/a');
const f = (a) => a;
f(1);
exports.s1 = function () { return a.helper(); }; // site s1
