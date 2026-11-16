const a = require('./lib/a');
for (const a of [1]) {}
exports.s1 = function () { return a.helper(); }; // site s1
