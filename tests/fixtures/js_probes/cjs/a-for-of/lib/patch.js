const a = require('./a');
for (const x of [a]) { x.helper = function () { return 'patched'; }; }
