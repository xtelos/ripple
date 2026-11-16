const C = require('./lib/c');
C.boot();
exports.s1 = function () { return C.make(); }; // site s1
