Object.getPrototypeOf({}).boot = function () { this.helper = function () { return 'patched'; }; };
