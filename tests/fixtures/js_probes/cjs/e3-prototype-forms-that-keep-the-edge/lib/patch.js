const EE = require('events');
function K() {}
K.prototype.m = function () {};
class C { }
C.prototype.n = function () {};
const viaK = Object.create(K.prototype);
const viaEE = Object.create(EE.prototype);
const viaArray = Object.create(Array.prototype);
const has = Object.prototype.hasOwnProperty.call({}, 'k');
const slice = Array.prototype.slice.call([1]);
K.prototype.constructor = K;
const o = { constructor: K, prototype: 1, 'process': 2 };
class D { constructor() { this.x = 1; } }
exports.keys = [viaK, viaEE, viaArray, has, slice, o, D];
