const a = require('./a');
const key = 'help' + 'er';
a[key] = function () { return 'lib/patch:computed'; };
