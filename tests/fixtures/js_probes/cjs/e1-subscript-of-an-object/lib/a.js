function helper() { return 'lib/a:helper'; }
const holder = { boot: function () { this.helper = function () { return 'patched'; }; } };
exports.helper = helper;
exports.boot = holder['boot'];
