class C {
  static make() { return 'lib/c:make'; }
  static boot() { this.make = function () { return 'patched'; }; }
}
module.exports = C;
