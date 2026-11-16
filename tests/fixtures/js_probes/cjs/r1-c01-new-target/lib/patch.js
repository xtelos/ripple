function F() { if (!new.target) return new F(); }
require('./a').helper = function () { return 'patched'; };
