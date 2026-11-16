const a = require('./a');
if (typeof a !== 'object') throw new Error('x');
if (a === undefined || a === null) throw new Error('y');
if (!a) throw new Error('z');
if (a) { a.helper.call(null); }
const h = a.helper;
h();
const { helper } = a;
helper();
const k = h;
k();
void a;
