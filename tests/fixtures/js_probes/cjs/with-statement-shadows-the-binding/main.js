const a = require('./lib/a');
with ({ a: { helper: function () { return 'main:with'; } } }) {
  exports.s1 = function () { return a.helper(); }; // site s1
}
