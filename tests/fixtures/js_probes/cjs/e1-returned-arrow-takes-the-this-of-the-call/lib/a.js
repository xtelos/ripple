function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = function () { return () => { this.helper = function () { return 'patched'; }; }; };
