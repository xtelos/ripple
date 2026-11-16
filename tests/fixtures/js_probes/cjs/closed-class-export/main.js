const C = require('./lib/c');
exports.s1 = function () { return new C().id; }; // site s1
exports.s2 = function () { return C.make(); }; // site s2
