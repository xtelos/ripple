function helper() { return 'lib/a:helper'; }
module.exports = { helper, [Symbol.toPrimitive]() { this.helper = function () { return 'patched'; }; return 1; } };
