function helper() { return 'lib/a:A'; }
if (true) { function helper() { return 'lib/a:B'; } }
module.exports = { helper };
