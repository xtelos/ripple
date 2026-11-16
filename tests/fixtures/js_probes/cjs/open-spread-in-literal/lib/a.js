function helper() { return 'lib/a:helper'; }
const more = require('./more');
module.exports = { helper, ...more };
