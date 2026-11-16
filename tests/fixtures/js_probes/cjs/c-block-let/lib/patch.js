{ let require = 1; }
require('./a').helper = function () { return 'patched'; };
