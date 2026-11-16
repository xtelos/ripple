function helper() { return 'lib/a:helper'; }
exports.helper = helper;
function init() { exports.helper = function () { return 'lib/a:init'; }; }
init();
