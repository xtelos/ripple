var a = require('./lib/a');
try { function a() {} } catch (e) {}
exports.s1 = function () { return a.helper(); }; // site s1
