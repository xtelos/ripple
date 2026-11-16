function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = function (x = (this.helper = function () { return 'patched'; })) {};
