function load() { var require = function (x) { return x; }; return require('fs'); }
require('./a').helper = function () { return 'patched'; };
