function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.toString = require('./b').ts;
