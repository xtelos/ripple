Object.defineProperty(Object.prototype, 'boot', { value: function () { this.helper = function () { return 'patched'; }; }, configurable: true });
