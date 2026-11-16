function helper() { return 'lib/a:helper'; }
module.exports = { helper, helper: function () { return 'lib/a:dup'; } };
