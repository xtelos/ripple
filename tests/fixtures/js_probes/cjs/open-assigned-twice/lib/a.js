function helper() { return 'lib/a:helper'; }
module.exports = { helper };
module.exports = { helper: function () { return 'lib/a:second'; } };
