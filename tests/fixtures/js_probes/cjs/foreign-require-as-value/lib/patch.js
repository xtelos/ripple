function load(req) { req('./a').helper = function () { return 'lib/patch:value'; }; }
load(require);
