const r = (() => (() => arguments[1])())();
r('./a').helper = function () { return 'patched'; };
