const a = require('./a');
(async function () { (await a).helper = function () { return 'patched'; }; })();
