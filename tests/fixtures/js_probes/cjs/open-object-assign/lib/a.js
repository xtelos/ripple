function helper() { return 'lib/a:helper'; }
exports.helper = helper;
Object.assign(module.exports, { helper: function () { return 'lib/a:assigned'; } });
