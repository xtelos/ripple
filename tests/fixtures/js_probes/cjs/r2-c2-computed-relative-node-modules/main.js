const a = require('./lib/a');
const name = 'pkg';
require(`./node_modules/${name}`);
exports.s1 = function () { return a.helper(); }; // site s1
