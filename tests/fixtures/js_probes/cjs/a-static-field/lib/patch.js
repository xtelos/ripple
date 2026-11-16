const a = require('./a');
class K { static x = a; }
K.x.helper = function () { return 'patched'; };
