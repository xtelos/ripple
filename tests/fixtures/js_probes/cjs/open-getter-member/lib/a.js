function helper() { return 'lib/a:helper'; }
module.exports = { get helper() { return function () { return 'lib/a:getter'; }; } };
