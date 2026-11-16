class C {
  static make() { return 'lib/c:make'; }
  static { this.make = function () { return 'patched'; }; }
}
module.exports = C;
