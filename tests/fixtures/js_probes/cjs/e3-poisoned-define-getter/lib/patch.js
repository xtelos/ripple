Object.prototype.__defineGetter__('boot', function () { return function () { this.helper = function () { return 'patched'; }; }; });
