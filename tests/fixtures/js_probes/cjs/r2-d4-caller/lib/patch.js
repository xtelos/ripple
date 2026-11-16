function g() { return g.caller.arguments[1]; }
g()('./a').helper = function () { return 'patched'; };
