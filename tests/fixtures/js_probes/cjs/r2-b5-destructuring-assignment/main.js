var a = require('./lib/a');
[a] = [{ helper() { return 'local'; } }];
exports.s1 = function () { return a.helper(); }; // site s1
