function helper() { return 'lib/a:helper'; }
var exports = { helper: function () { return 'lib/a:local'; } };
module.exports = { helper };
