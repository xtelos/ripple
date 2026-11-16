const pkg = require('pkg');
const { f } = require('pkg');
const path = require('path');
exports.s1 = function () { return pkg.f(); }; // site s1
exports.s2 = function () { return f(); }; // site s2
exports.s3 = function () { return path.join('a', 'b'); }; // site s3
