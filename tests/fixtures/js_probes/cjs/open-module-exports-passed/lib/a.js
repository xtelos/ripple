function helper() { return 'lib/a:helper'; }
function install(target) { target.helper = function () { return 'lib/a:installed'; }; }
module.exports = { helper };
install(module.exports);
