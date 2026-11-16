function helper() { return 'lib/a:helper'; }
module.exports = { helper, boot() { super.helper = function () { return 'patched'; }; } };
