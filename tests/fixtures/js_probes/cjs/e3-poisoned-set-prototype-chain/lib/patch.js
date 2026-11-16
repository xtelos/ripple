const child = Object.create(null);
Object.setPrototypeOf(child, Object.prototype);
Object.getPrototypeOf(child).boot = function () { this.helper = function () { return 'patched'; }; };
