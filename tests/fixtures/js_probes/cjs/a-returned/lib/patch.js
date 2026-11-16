const a = require('./a');
function get() { return a; }
get().helper = function () { return 'patched'; };
