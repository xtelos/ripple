function helper() { return 'lib/a:helper'; }
function other() { return 'lib/a:other'; }
const arrow = () => 1;
module.exports = { helper, other, n: 1, neg: -1, s: 'x', t: `y`, yes: true, no: false, nothing: null, arrow, inline: function () { return 2; }, method() { return 3; } };
