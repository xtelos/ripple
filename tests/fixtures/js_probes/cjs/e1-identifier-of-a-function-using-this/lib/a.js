function helper() { return 'lib/a:helper'; }
const fn = function () { this.helper = function () { return 'patched'; }; };
exports.helper = helper;
exports.boot = fn;
