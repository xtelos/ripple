const { helper = function () { return 'default'; } } = require('./lib/a');
exports.s1 = function () { return helper(); }; // site s1
