const a = require('./lib/a');
function other(a) { return a; }
other(1);
exports.s1 = function () { return a.helper(); }; // site s1
