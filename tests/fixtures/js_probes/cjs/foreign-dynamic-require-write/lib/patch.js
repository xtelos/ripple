const name = './a';
const a = require(name);
a.helper = function () { return 'lib/patch:dynamic'; };
