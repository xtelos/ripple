const a = require('./lib/a');
a.me().helper = function () { return 'patched'; };
exports.s1 = function () { return a.helper(); }; // site s1
