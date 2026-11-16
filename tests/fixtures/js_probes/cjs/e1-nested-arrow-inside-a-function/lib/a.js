function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = function () { const f = () => { this.helper = function () { return 'patched'; }; }; f(); };
