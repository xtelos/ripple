function helper() { return 'lib/a:helper'; }
exports.helper = helper;
if (true) {
  exports.helper = function () { return 'lib/a:conditional'; };
}
