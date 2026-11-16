function helper() { return 'lib/a:helper'; }
var boot;
boot = function () { this.helper = function () { return 'patched'; }; };
module.exports = { helper, boot };
