function helper() { return 'lib/a:helper'; }
exports.helper = helper;
exports.inst = new (class P { toString() { this.n = 1; return 'x'; } })();
