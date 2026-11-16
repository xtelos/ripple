class C { static make() { return 'lib/c:C.make'; } }
function other() { return 'lib/c:other'; }
C.make = other;
module.exports = C;
