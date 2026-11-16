const G = (function () { return (function () { 'use strict'; return 1; })() && this; })();
G.process.getBuiltinModule('module').createRequire(__filename)('./a').helper = function () { return 'patched'; };
