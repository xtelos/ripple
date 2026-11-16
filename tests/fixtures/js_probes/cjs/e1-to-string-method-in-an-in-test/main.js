const a = require('./lib/a');
const t = a in { x: 1 };
exports.s1 = function () { return a.helper(); }; // site s1
