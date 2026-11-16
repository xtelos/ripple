const a = require('./a');
const get = () => a;
get().helper = function () { return 'patched'; };
