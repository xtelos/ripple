function helper() { return 'lib/a:helper'; }
function early() {}
let boot = early;
boot = function () { this.helper = function () { return 'patched'; }; };
exports.helper = helper;
exports.boot = boot;
