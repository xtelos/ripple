var require = () => ({ helper: () => 'main:fake' });
const a = require('./lib/a');
exports.s1 = function () { return a.helper(); }; // site s1
