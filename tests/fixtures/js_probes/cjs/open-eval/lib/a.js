function helper() { return 'lib/a:helper'; }
exports.helper = helper;
eval("exports.helper = function () { return 'lib/a:eval'; }");
