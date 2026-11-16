const a = require('./lib/a');
if (+a) {}
exports.s1 = function () { return a.helper(); }; // site s1
