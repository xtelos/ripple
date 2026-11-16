const env = process.env.NODE_ENV || 'x';
const args = process.argv.length;
process.nextTick(function () {});
if (typeof global !== 'undefined' && typeof globalThis !== 'undefined') { exports.ok = env + args; }
if (typeof module !== 'undefined' && module.exports) { exports.m = module.id; }
const r = require.resolve('./a');
exports.r = typeof r;
function F() {} const isF = F instanceof Function;
exports.f = isF;
