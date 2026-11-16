function helper() { return 'lib/a:helper'; }
function make() { return function () { this.helper = function () { return 'patched'; }; }; }
const boot = make();
module.exports = { helper, boot };
