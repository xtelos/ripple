function helper() { return 'main:helper'; }
{ function helper() { return 'main:helper2'; } }
exports.s1 = function () { return helper(); }; // site s1
