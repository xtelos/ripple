function helper() { return 'lib/a:helper'; }
exports.helper = function () { return 'lib/a:dead'; };
module.exports = { helper };
