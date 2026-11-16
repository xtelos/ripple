function helper() { return 'lib/a:helper'; }
module.exports = { helper, get boot() { this.helper = function () { return 'patched'; }; return 1; } };
