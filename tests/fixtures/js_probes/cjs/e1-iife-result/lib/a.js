function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = (function () { return function () { this.helper = function () { return 'patched'; }; }; })();
