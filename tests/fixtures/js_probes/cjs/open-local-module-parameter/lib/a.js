function helper() { return 'lib/a:helper'; }
function wrap(module) { module.exports = { helper: function () { return 'lib/a:wrapped'; } }; }
module.exports = { helper };
wrap({});
