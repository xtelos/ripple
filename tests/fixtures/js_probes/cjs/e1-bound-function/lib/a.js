function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = (function () { this.helper = function () { return 'patched'; }; }).bind(undefined);
