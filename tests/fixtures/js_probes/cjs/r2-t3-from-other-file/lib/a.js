function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.boot = require('./b').boot;
