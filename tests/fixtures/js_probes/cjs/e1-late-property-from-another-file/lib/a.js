function helper() { return 'lib/a:helper'; }
module.exports = { helper };
module.exports.boot = require('./b').boot;
