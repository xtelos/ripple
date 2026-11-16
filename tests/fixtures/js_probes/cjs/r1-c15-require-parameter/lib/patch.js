function load(require) { return require('fs'); }
require('./a').helper = function () { return 'patched'; };
