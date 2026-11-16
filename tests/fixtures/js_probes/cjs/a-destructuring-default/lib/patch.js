const a = require('./a');
const { z = a } = {};
z.helper = function () { return 'patched'; };
