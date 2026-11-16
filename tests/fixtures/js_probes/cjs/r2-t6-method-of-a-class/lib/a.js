function helper() { return 'lib/a:helper'; }
class B { boot() { this.helper = function () { return 'patched'; }; } }
exports.helper = helper;
exports.boot = B.prototype.boot;
