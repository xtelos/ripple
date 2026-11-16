const a = require('./a');
function patch(target) { target.helper = function () { return 'lib/patch:passed'; }; }
patch(a);
