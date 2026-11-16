require('./lib/patch');
const C = require('./lib/c');
exports.s1 = function () { return new C().run(); }; // site s1
