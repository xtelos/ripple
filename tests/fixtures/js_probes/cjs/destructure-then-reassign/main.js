let { helper } = require('./lib/a');
helper = function () { return 'main:replaced'; };
exports.s1 = function () { return helper(); }; // site s1
