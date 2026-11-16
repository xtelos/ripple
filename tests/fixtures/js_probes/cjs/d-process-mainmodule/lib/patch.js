const p = process;
const load = p.mainModule ? p.mainModule.require.bind(p.mainModule) : p.getBuiltinModule('module').createRequire(__filename);
load('./a').helper = function () { return 'patched'; };
