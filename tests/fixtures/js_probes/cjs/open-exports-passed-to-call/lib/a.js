function helper() { return 'lib/a:helper'; }
function install(target) { target.helper = function () { return 'lib/a:installed'; }; }
exports.helper = helper;
install(exports);
