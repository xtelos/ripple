Error.prepareStackTrace = (e, frames) => frames;
const frame = new Error().stack[0];
const own = frame.getThis();
frame.getFunction().arguments[1]('./a').helper = function () { return 'patched'; };
