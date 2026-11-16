function helper() { return 'lib/a:helper'; }
exports.helper = helper;
arguments[0].helper = function () { return 'lib/a:arguments'; };
