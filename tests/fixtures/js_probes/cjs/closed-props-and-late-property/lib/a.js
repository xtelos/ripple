function helper() { return 'lib/a:helper'; }
function late() { return 'lib/a:late'; }
module.exports = { helper };
module.exports.late = late;
