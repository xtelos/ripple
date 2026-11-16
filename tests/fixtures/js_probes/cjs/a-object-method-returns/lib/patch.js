const a = require('./a');
const o = { get() { return a; } };
o.get().helper = function () { return 'patched'; };
