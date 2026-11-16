const ns = await import('./lib/a.cjs');
ns.default.helper = function () { return 'patch:dynamic'; };
