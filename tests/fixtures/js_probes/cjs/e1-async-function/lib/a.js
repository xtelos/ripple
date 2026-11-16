function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = async function () { this.helper = function () { return 'patched'; }; };
