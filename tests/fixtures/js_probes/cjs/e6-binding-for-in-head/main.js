var a = require('./lib/a');
for (var a in { x: 1 }) {}
exports.s1 = function () { return a.helper(); }; // site s1
