const proto = Object.prototype;
proto.boot = function () { this.helper = function () { return 'patched'; }; };
