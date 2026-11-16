const a = require('./lib/a');
const where = require.resolve('./lib/a');
exports.s1 = function () { return a.helper(); }; // site s1
