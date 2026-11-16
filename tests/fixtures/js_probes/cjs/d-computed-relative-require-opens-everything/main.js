const a = require('./lib/a');
const name = 'other';
const x = require('./lib/' + name);
exports.s1 = function () { return a.helper(); }; // site s1
exports.s2 = function () { return x.other(); }; // site s2
