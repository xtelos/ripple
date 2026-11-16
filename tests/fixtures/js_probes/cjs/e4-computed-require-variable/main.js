const a = require('./lib/a');
const where = './lib/patch2.txt';
require(where);
exports.s1 = function () { return a.helper(); }; // site s1
