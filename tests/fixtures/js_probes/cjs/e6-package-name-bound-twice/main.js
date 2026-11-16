const pkg = require('pkg');
{ function pkg() { return { f() { return 'local'; } }; } }
exports.s1 = function () { return pkg.f(); }; // site s1
