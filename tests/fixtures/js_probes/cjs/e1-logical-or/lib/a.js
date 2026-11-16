function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = null || function () { this.helper = function () { return 'patched'; }; };
