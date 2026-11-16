Object.assign(Object.prototype, { boot: function () { this.helper = function () { return 'patched'; }; } });
