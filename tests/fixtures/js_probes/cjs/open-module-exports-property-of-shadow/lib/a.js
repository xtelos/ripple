function helper() { return 'lib/a:helper'; }
exports.helper = helper;
function f() { var exports = {}; exports.helper = 1; return exports; }
