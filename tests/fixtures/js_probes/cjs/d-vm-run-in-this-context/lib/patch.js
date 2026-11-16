require('vm').runInThisContext("process.getBuiltinModule('module').createRequire(process.cwd() + '/x.js')('./lib/a').helper = function () { return 'patched'; }");
