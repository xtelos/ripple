'use strict';
const p = process.platform + process.version;
process.stdout.write('');
process.on('exit', function () {});
process.exitCode = 0;
const t = typeof process + typeof require + typeof module + typeof Function;
const c = Function.prototype.call;
class K {}
const name = new K().constructor.name;
const same = new K().constructor === K;
const pr = K.prototype;
function E() { Error.captureStackTrace(this, this.constructor); }
global.setTimeout(function () {}, 0);
globalThis.console.log === undefined;
global.counter = 1;
const main = require.main === module;
const id = module.id + module.filename + module.loaded + module.path;
const top = !module.parent;
const here = module.parent === null;
exports.z = [p, t, c, name, same, pr, E, main, id, top, here];
