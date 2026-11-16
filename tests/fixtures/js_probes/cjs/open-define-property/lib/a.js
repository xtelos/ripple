function helper() { return 'lib/a:helper'; }
exports.helper = helper;
Object.defineProperty(exports, 'helper', { value: function () { return 'lib/a:defined'; } });
