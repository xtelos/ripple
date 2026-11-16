const a = require('./lib/a');
const name = '.hidden/patch';
require('./' + name);
exports.s1 = function () { return a.helper(); }; // site s1
