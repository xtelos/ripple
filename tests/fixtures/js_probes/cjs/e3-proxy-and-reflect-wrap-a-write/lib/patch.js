const target = {};
const proxy = new Proxy(target, {});
Reflect.set(proxy, 'x', 1);
