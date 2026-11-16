const O = Object;
O.prototype.boot = function () { this.helper = function () { return 'patched'; }; };
