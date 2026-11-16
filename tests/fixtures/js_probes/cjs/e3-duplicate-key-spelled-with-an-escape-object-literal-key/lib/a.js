function helper() { return 'lib/a:helper'; }
function second() { return 'lib/a:second'; }
module.exports = { helper, 'h\x65lper': second };
