let helper = function () { return 'lib/a:helper'; };
function other() { return 'lib/a:other'; }
module.exports = { helper };
helper = other;
