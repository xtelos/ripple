class X {}
Object.getPrototypeOf(X.prototype).boot = function () { this.helper = function () { return 'patched'; }; };
