const { createRequire } = require('module');
const r = createRequire(__filename);
r('./a').helper = function () { return 'lib/patch:createRequire'; };
