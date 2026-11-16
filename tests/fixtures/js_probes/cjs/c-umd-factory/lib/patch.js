(function (root, factory) { factory(root); })(this, function (require) { return 1; });
require('./a').helper = function () { return 'patched'; };
