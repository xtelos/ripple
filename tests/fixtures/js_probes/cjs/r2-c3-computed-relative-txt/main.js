const a = require('./lib/a');
const ext = '.txt';
require('./lib/patch' + ext);
exports.s1 = function () { return a.helper(); }; // site s1
