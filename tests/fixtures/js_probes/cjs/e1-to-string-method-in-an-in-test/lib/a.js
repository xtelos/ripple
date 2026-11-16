function helper() { return 'lib/a:helper'; }
module.exports = { helper, toString() { this.helper = function () { return 'patched'; }; return 'x'; } };
