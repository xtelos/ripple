function helper() { return 'lib/a:helper'; }
function boot() {}
function boot() { this.helper = function () { return 'patched'; }; }
exports.helper = helper;
exports.boot = boot;
