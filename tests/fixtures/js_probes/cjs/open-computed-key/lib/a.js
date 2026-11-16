function helper() { return 'lib/a:helper'; }
const key = 'hel' + 'per';
module.exports = { helper, [key]: function () { return 'lib/a:computed'; } };
