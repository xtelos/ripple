function helper() { return 'lib/a:helper'; }
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { helper: function () { return 'lib/a:umd'; } };
}
