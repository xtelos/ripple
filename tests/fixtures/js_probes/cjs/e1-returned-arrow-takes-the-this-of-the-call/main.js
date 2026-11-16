const a = require('./lib/a');
a.boot()();
exports.s1 = function () { return a.helper(); }; // site s1
