const C = require('./c');
new C().constructor.make = function () { return 'patched'; };
