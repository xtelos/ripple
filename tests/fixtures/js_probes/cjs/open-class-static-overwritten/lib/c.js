class C { static make() { return 'lib/c:C.make'; } }
module.exports = C;
module.exports.make = function () { return 'lib/c:replaced'; };
