function helper() { return 'lib/a:helper'; }
module.exports = { helper };
exports.helper = function () { return 'lib/a:dead'; };
