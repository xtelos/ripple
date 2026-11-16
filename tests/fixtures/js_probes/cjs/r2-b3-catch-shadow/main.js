const a = require('./lib/a');
try { throw { helper() { return 'x'; } }; } catch (a) { a.helper = 1; }
exports.s1 = function () { return a.helper(); }; // site s1
