import { createRequire } from 'module';
createRequire(import.meta.url)('./lib/a.cjs').helper = function () { return 'patched'; };
