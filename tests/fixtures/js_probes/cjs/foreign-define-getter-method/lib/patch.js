const a = require('./a');
a.__defineGetter__('helper', function () { return function () { return 'lib/patch:getter'; }; });
