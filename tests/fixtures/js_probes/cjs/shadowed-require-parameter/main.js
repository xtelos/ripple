const a = require('./lib/a');
function run(require) {
  const b = require('./lib/a');
  return b.helper(); // site s1
}
exports.s1 = function () { return run(function () { return { helper: function () { return 'main:fake'; } }; }); };
exports.s2 = function () { return a.helper(); }; // site s2
