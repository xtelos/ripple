function helper() { return 'lib/a:helper'; }
function boot() { this.helper = function () { return 'lib/a:booted'; }; }
module.exports = { helper, boot };
