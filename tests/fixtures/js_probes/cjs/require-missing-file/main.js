exports.s1 = function () {
  try {
    const a = require('./lib/nope');
    return a.helper(); // site s1
  } catch (e) {
    throw e;
  }
};
