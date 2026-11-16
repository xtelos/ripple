function F() { return 1; }
require('./a').helper = function () { return 'patched'; };
