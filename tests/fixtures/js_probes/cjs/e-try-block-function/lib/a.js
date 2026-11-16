try { function helper() { return 'lib/a:B'; } } catch (e) { }
function helper() { return 'lib/a:A'; }
module.exports = { helper };
