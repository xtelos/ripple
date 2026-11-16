const p = require('selfpkg/x');
exports.s1 = function () { return p.helper(); }; // site s1
exports.s2 = function () { const q = require('#int'); return q.helper(); }; // site s2
