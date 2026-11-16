const a = require('./a');
a.valueOf().helper = function () { return 'patched'; };
