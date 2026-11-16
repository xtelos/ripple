const load = (require) => require('fs');
require('./a').helper = function () { return 'patched'; };
