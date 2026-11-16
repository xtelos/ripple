switch (1) { case 1: function helper() { return 'lib/a:B'; } }
function helper() { return 'lib/a:A'; }
module.exports = { helper };
