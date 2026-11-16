function helper() { return 'lib/a:helper'; }
exports.helper = helper;
arguments[2].exports.helper = function () { return 'lib/a:module'; };
