const a = process.argv.length > 99 ? require('./lib/a') : require('./lib/b');
exports.s1 = function () { return a.helper(); }; // site s1
