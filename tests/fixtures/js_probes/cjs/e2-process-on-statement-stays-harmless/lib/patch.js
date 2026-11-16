process.on('exit', function () {});
process.once('exit', () => {});
process.off('exit', function () {});
process.stdout.on('error', function () {}).write('');
process.nextTick(function () {});
const n = process.cwd().length + process.hrtime().length;
