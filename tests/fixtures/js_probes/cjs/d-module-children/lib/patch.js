require('./a');
module.children[0].exports.helper = function () { return 'patched'; };
