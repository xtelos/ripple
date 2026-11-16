function helper() { return 'lib/a:helper'; }
const { boot } = require('./b');
exports.helper = helper;
exports.boot = boot;
