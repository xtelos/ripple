const a = require('./a');
function f(x = a) { x.helper = function () { return 'patched'; }; }
f();
