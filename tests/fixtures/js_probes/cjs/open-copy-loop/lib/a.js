function helper() { return 'lib/a:helper'; }
exports.helper = helper;
const more = require('./more');
Object.keys(more).forEach(function (k) { exports[k] = more[k]; });
