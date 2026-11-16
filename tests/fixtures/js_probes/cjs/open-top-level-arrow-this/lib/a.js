function helper() { return 'lib/a:helper'; }
exports.helper = helper;
const swap = () => { this.helper = function () { return 'lib/a:arrow'; }; };
swap();
