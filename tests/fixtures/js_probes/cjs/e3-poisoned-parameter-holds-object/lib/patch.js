function poison(o) { o.prototype.boot = function () { this.helper = function () { return 'patched'; }; }; }
poison(Object);
