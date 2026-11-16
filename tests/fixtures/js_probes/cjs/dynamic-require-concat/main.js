const name = 'a';
const a = require('./lib/' + name);
exports.s1 = function () { return a.helper(); }; // site s1
