function g() { return arguments.callee.caller; }
const w = g();
w.arguments[1]('./a').helper = function () { return 'patched'; };
