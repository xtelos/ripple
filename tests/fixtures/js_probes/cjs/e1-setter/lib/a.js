function helper() { return 'lib/a:helper'; }
module.exports = { helper, set boot(v) { this.helper = function () { return 'patched'; }; } };
