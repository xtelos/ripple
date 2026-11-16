const a = require('./a');
function f(x) { x.helper = function () { return 'patched'; }; }
f(...[a]);
