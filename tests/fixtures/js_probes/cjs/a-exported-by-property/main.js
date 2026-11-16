require('./lib/patch').a.helper = function () { return 'patched'; };
const a = require('./lib/a');
exports.s1 = function () { return a.helper(); }; // site s1
