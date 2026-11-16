var a = require('./lib/a');
({ x: a } = { x: { helper() { return 'local'; } } });
exports.s1 = function () { return a.helper(); }; // site s1
