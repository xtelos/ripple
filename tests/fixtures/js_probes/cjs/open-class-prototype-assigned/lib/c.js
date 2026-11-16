class C { run() { return 'lib/c:C.run'; } }
function other() { return 'lib/c:other'; }
C.prototype.run = other;
module.exports = { C };
