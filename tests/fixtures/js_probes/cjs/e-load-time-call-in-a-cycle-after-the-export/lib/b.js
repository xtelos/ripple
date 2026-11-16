const a = require('./a');
try { exports.s1 = { reached: a.x() }; } catch (e) { exports.s1 = { threw: e.name }; } // site s1
