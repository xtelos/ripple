exports.s1 = function () {
  if (true) {
    const a = require('./lib/a');
    return a.helper(); // site s1
  }
};
