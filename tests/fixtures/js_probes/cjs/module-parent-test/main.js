const a = require('./lib/a');
if (!module.parent) {
  console.log('main');
}
exports.s1 = function () { return a.helper(); }; // site s1
