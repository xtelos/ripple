const a = require('./lib/a');
try { throw 1; } catch ({ a }) {}
exports.s1 = function () { return a.helper(); }; // site s1
