function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = () => { this.helper = function () { return 'patched'; }; };
