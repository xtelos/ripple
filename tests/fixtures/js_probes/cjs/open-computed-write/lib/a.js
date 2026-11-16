function helper() { return 'lib/a:helper'; }
exports.helper = helper;
const name = 'helper';
exports[name] = function () { return 'lib/a:computed'; };
