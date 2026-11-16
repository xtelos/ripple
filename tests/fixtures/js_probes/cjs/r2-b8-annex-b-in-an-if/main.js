var a = require('./lib/a');
if (true) { function a() {} }
exports.s1 = function () { return a.helper(); }; // site s1
