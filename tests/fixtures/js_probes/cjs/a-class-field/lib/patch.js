const a = require('./a');
class K { x = a; }
new K().x.helper = function () { return 'patched'; };
