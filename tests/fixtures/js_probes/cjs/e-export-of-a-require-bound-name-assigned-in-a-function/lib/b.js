let { helper } = require('./a');
function swap() { helper = function () { return 'lib/b:swapped'; }; }
swap();
exports.helper = helper;
