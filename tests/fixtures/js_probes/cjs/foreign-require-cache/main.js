const a = require('./lib/a');
require('./lib/patch');
exports.s1 = function () { return a.helper(); }; // site s1
