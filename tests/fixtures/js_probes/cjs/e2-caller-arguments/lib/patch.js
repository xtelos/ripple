function g() { return g.caller.arguments[2]; }
g().require('./a').helper = function () { return 'patched'; };
