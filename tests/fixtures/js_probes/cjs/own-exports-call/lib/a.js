function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.s1 = function () { return exports.helper(); }; // site s1
