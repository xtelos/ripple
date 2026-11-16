let a;
if (process.argv.length > 99) {
  a = require('./lib/a');
} else {
  a = require('./lib/b');
}
exports.s1 = function () { return a.helper(); }; // site s1
