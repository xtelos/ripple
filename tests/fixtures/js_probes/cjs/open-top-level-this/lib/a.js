function helper() { return 'lib/a:helper'; }
exports.helper = helper;
this.helper = function () { return 'lib/a:this'; };
