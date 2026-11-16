Reflect.getPrototypeOf({}).boot = function () { this.helper = function () { return 'patched'; }; };
