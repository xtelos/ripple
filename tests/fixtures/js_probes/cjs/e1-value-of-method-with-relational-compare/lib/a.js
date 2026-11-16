function helper() { return 'lib/a:helper'; }
module.exports = { helper, valueOf() { this.helper = function () { return 'patched'; }; return 1; } };
