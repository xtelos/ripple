const a = require('./a');
const k = '__defineGetter__';
a[k]('helper', function () { return function () { return 'patched'; }; });
