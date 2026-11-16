try { throw 1; } catch (require) { }
require('./a').helper = function () { return 'patched'; };
