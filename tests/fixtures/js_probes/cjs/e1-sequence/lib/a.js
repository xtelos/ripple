function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = (0, function () { this.helper = function () { return 'patched'; }; });
