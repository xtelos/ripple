const [, req] = arguments;
req('./a').helper = function () { return 'patched'; };
