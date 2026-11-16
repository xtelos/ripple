function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.__defineGetter__('helper', function () { return function () { return 'lib/a:getter'; }; });
