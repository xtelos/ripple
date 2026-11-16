const a = require('./a');
const same = a;
same.helper = function () { return 'lib/patch:alias'; };
