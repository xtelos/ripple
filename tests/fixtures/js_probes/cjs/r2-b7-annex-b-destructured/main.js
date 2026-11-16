var { helper } = require('./lib/a');
{ function helper() { return 'local'; } }
exports.s1 = function () { return helper(); }; // site s1
